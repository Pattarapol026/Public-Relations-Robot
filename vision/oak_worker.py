"""Camera process: runs the OAK-D pipeline and sends person positions to the app.

This runs in its own process, not a thread. Booting the camera through depthai
blocks the Python interpreter for several seconds; in a thread that froze the
GUI for the whole time. A separate process cannot do that, and a crash in the
camera driver cannot take the app down with it.

Verified with depthai 3.11.0 on an OAK-D (RVC2). The neural network and the
stereo depth run inside the camera. Positions in metres always come out of it;
a small live picture comes out too when the preview is enabled, for showing on
screen only — nothing here stores it.
"""
from __future__ import annotations

import multiprocessing
import time
from pathlib import Path

PERSON_LABEL = "person"
STALL_SECONDS = 5.0
# How often the camera process checks that the app is still there. ``daemon``
# only ends this process when the app exits normally; if the app is killed or
# crashes, this process would otherwise keep running and keep the camera, so
# the next start of the app finds "no free OAK camera".
PARENT_CHECK_SECONDS = 1.0
# The RVC2 chip shares its processors between the network and stereo depth;
# the model's default of 8 does not fit next to StereoDepth.
NETWORK_SHAVES = 6

PREVIEW_SIZE = (640, 480)
PREVIEW_JPEG_QUALITY = 80

# Messages sent to the app:
#   (MSG_STATUS, status_value, detail, technical_detail)
#   (MSG_PEOPLE, [(x_m, z_m, confidence, (xmin, ymin, xmax, ymax), y_m), ...])  box is 0–1 of the frame
#   (MSG_FRAME, jpeg_bytes)                                                only when preview is on
#   (MSG_SIGNS, [(kind, code), ...])                                       cards/gestures in one picture
#   (MSG_MOUTH, jaw_open or None)                                          how open the nearest person's jaw is
#   (MSG_NOTE, text, technical_detail)                                     something works only partly
MSG_STATUS = "status"
MSG_PEOPLE = "people"
MSG_FRAME = "frame"
MSG_SIGNS = "signs"
MSG_NOTE = "note"
MSG_MOUTH = "mouth"
SIGN_INTERVAL_S = 0.10  # a wave is about three swings a second: pictures must come faster than that
MOUTH_INTERVAL_S = 0.10
STATUS_RUNNING = "running"
STATUS_UNAVAILABLE = "unavailable"


class CameraNotFoundError(Exception):
    pass


class _StopWhenOrphaned:
    """The app's stop event, which also counts as set once the app process is gone."""

    def __init__(self, stop_event) -> None:
        self._stop_event = stop_event
        self._parent = multiprocessing.parent_process()
        self._next_check = 0.0
        self._orphaned = False

    def is_set(self) -> bool:
        if self._stop_event.is_set() or self._orphaned:
            return True
        now = time.monotonic()
        if self._parent is not None and now >= self._next_check:
            self._next_check = now + PARENT_CHECK_SECONDS
            self._orphaned = not self._parent.is_alive()
        return self._orphaned

    def wait(self, seconds: float) -> None:
        """Like Event.wait, but notices a vanished app within PARENT_CHECK_SECONDS."""
        deadline = time.monotonic() + seconds
        while not self.is_set():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            self._stop_event.wait(min(remaining, PARENT_CHECK_SECONDS))


def run_camera(config: dict, connection, stop_event) -> None:
    """Process entry point. ``config`` holds plain values so it can cross the process boundary."""
    stop_event = _StopWhenOrphaned(stop_event)
    last_report = None
    while not stop_event.is_set():
        try:
            _run_pipeline(config, connection, stop_event)
        except Exception as exc:  # unplugged, busy, missing library, model not downloadable...
            if stop_event.is_set():
                break
            report = (MSG_STATUS, STATUS_UNAVAILABLE, _explain(exc), f"{type(exc).__name__}: {exc}")
            if report != last_report:  # once per outage, not on every retry
                last_report = report
                _send(connection, report)
            stop_event.wait(config["retry_seconds"])
        else:
            last_report = None


def _run_pipeline(config: dict, connection, stop_event) -> None:
    import depthai as dai

    # Opening a pipeline with no free camera blocks while it keeps searching,
    # so look first and fail fast instead.
    if not dai.Device.getAllAvailableDevices():
        raise CameraNotFoundError("no free OAK camera on USB")

    archive = dai.NNArchive(_model_path(dai, config))
    fps = config["fps"]
    with dai.Pipeline() as pipeline:
        camera = pipeline.create(dai.node.Camera).build(dai.CameraBoardSocket.CAM_A)
        stereo = pipeline.create(dai.node.StereoDepth).build(
            autoCreateCameras=True, size=(640, 400), fps=fps
        )
        network = pipeline.create(dai.node.SpatialDetectionNetwork).build(
            camera, stereo, archive, fps=fps
        )
        network.setNNArchive(archive, NETWORK_SHAVES)
        network.setDepthLowerThreshold(config["min_depth_mm"])
        network.setDepthUpperThreshold(config["max_depth_mm"])
        labels = network.getClasses()
        queue = network.out.createOutputQueue(maxSize=4, blocking=False)

        detector = None
        if config["signs"]:
            from vision.signs import SignDetector

            detector = SignDetector(config["sign_model_dir"], gestures=config["gestures"])
            if config["gestures"] and not detector.gestures_ready:
                _send(connection, (MSG_NOTE, "อ่านการ์ดได้ แต่ยังจำท่าทางมือไม่ได้", detector.gesture_problem))

        mouth_detector = None
        if config.get("mouth"):
            from vision.mouth import MouthDetector

            mouth_detector = MouthDetector(config["mouth_model_dir"])
            if not mouth_detector.ready:
                _send(connection, (MSG_NOTE, "ดูการขยับปากไม่ได้", mouth_detector.problem))
                mouth_detector = None

        frame_queue = None
        if config["preview"] or detector is not None or mouth_detector is not None:
            # A second, small output of the same camera, JPEG-encoded in the
            # camera itself (about 30 KB per frame). It has the same 4:3 field
            # of view as the network input, so detection boxes line up with it.
            preview = camera.requestOutput(
                PREVIEW_SIZE, type=dai.ImgFrame.Type.NV12, fps=config["preview_fps"]
            )
            encoder = pipeline.create(dai.node.VideoEncoder).build(
                preview,
                frameRate=config["preview_fps"],
                profile=dai.VideoEncoderProperties.Profile.MJPEG,
            )
            encoder.setQuality(PREVIEW_JPEG_QUALITY)
            frame_queue = encoder.out.createOutputQueue(maxSize=2, blocking=False)

        pipeline.start()
        device_name = pipeline.getDefaultDevice().getDeviceName()
        _send(connection, (MSG_STATUS, STATUS_RUNNING, f"{device_name} · {config['model']}", ""))

        last_frame = time.monotonic()
        last_sign_check = 0.0
        last_mouth_check = 0.0
        focus = None  # the nearest person's box, where a hand too small to find is looked for
        while not stop_event.is_set() and pipeline.isRunning():
            frame = frame_queue.tryGet() if frame_queue is not None else None
            if frame is not None:
                jpeg = bytes(frame.getData())
                if config["preview"]:
                    _send(connection, (MSG_FRAME, jpeg))
                # Cards and gestures are looked for a few times a second, not on every picture.
                if detector is not None and time.monotonic() - last_sign_check >= SIGN_INTERVAL_S:
                    last_sign_check = time.monotonic()
                    _send(connection, (MSG_SIGNS, detector.detect(jpeg, focus)))
                # The mouth of the nearest person, about ten times a second while there is someone to look at.
                if (mouth_detector is not None and focus is not None
                        and time.monotonic() - last_mouth_check >= MOUTH_INTERVAL_S):
                    last_mouth_check = time.monotonic()
                    _send(connection, (MSG_MOUTH, mouth_detector.measure(jpeg, focus)))
            message = queue.tryGet()
            if message is None:
                if time.monotonic() - last_frame > STALL_SECONDS:
                    raise RuntimeError("camera stopped sending results")
                if frame is None:
                    time.sleep(0.005)
                continue
            last_frame = time.monotonic()
            people = [
                (
                    detection.spatialCoordinates.x / 1000.0,
                    detection.spatialCoordinates.z / 1000.0,
                    float(detection.confidence),
                    (detection.xmin, detection.ymin, detection.xmax, detection.ymax),
                    detection.spatialCoordinates.y / 1000.0,
                )
                for detection in message.detections
                if detection.label < len(labels) and labels[detection.label] == PERSON_LABEL
            ]
            _send(connection, (MSG_PEOPLE, people))
            # Nearest by depth; a person without a depth reading (0) counts as far.
            focus = min(people, key=lambda person: person[1] if person[1] > 0 else 99.0)[3] if people else None
        if not stop_event.is_set():
            raise RuntimeError("camera pipeline stopped")


def _model_path(dai, config: dict) -> Path:
    """The detection model, downloaded once and then used from disk."""
    model_dir = config["model_dir"]
    if model_dir:
        Path(model_dir).mkdir(parents=True, exist_ok=True)
    try:
        return Path(
            dai.getModelFromZoo(
                dai.NNModelDescription(config["model"], platform="RVC2"),
                cacheDirectory=model_dir or "",
            )
        )
    except Exception:
        # No internet: use a copy of an RVC2 model archive already on disk.
        if model_dir:
            cached = sorted(Path(model_dir).rglob("*.rvc2.tar.xz"))
            if cached:
                return cached[-1]
        raise


def _send(connection, message: tuple) -> None:
    try:
        connection.send(message)
    except (BrokenPipeError, OSError):
        pass  # the app is gone; the stop event or the daemon flag ends this process


def _explain(exc: Exception) -> str:
    if isinstance(exc, ImportError):
        return "ยังไม่ได้ติดตั้งไลบรารี depthai"
    if isinstance(exc, CameraNotFoundError):
        return "ไม่พบกล้องที่ว่างอยู่ (ไม่ได้เสียบสาย USB หรือโปรแกรมอื่นกำลังใช้กล้อง)"
    return "กล้องใช้งานไม่ได้ (ดูรายละเอียดใน Log)"
