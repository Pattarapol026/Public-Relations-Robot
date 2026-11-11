"""Vision source backed by a Luxonis OAK-D running in a separate process.

See ``vision/oak_worker.py`` for the camera pipeline and for why it is a
process rather than a thread.
"""
from __future__ import annotations

import logging
import multiprocessing
import time
from dataclasses import dataclass
from pathlib import Path

from vision import oak_worker
from vision.interfaces import PersonObservation, SourceStatus, VisionSnapshot, VisionSource
from vision.signs import Sign

logger = logging.getLogger(__name__)

# Closing the pipeline politely can take several seconds with the preview
# encoder running. If it takes longer than this the process is ended instead;
# the camera is released either way.
STOP_WAIT_SECONDS = 2.5
# If the camera process itself dies (a driver crash, not an unplugged cable,
# which the process handles on its own), it is started again this many times.
MAX_PROCESS_RESTARTS = 5


@dataclass(frozen=True)
class OakConfig:
    model: str = "yolov6-nano"
    fps: int = 10
    model_dir: Path | None = None
    min_depth_mm: int = 300
    max_depth_mm: int = 8000
    retry_seconds: float = 4.0
    preview: bool = True  # also send a live picture for the screen
    preview_fps: int = 20
    signs: bool = True  # look for marker cards (and gestures) in the picture
    gestures: bool = True
    sign_model_dir: Path | None = None


class OakVisionSource(VisionSource):
    def __init__(self, config: OakConfig) -> None:
        self._config = config
        self._snapshot: VisionSnapshot | None = None
        self._frame: bytes | None = None
        self._sign_cycles: list[list[Sign]] = []
        self._note = ""
        self._status = SourceStatus.DISABLED
        self._detail = ""
        self._process: multiprocessing.Process | None = None
        self._connection = None
        self._stop_event = None
        self._restarts = 0
        self._restart_at: float | None = None

    def start(self) -> None:
        if self._process is not None:
            return
        config = self._config
        # "spawn" on every platform: the child starts clean and imports only the worker.
        context = multiprocessing.get_context("spawn")
        self._connection, child_connection = context.Pipe(duplex=False)
        self._stop_event = context.Event()
        self._process = context.Process(
            target=oak_worker.run_camera,
            args=(
                {
                    "model": config.model,
                    "fps": config.fps,
                    "model_dir": str(config.model_dir) if config.model_dir else "",
                    "min_depth_mm": config.min_depth_mm,
                    "max_depth_mm": config.max_depth_mm,
                    "retry_seconds": config.retry_seconds,
                    "preview": config.preview,
                    "preview_fps": config.preview_fps,
                    "signs": config.signs,
                    "gestures": config.gestures,
                    "sign_model_dir": str(config.sign_model_dir) if config.sign_model_dir else "",
                },
                child_connection,
                self._stop_event,
            ),
            name="oak-camera",
            daemon=True,  # never outlives the app, even if the app crashes
        )
        self._process.start()
        child_connection.close()
        self._status, self._detail = SourceStatus.STARTING, "กำลังเชื่อมต่อกล้อง"

    def stop(self) -> None:
        self._restart_at = None
        process, self._process = self._process, None
        if process is not None:
            self._stop_event.set()
            process.join(STOP_WAIT_SECONDS)
            if process.is_alive():
                logger.info("Camera process still closing after %.1f s; ending it", STOP_WAIT_SECONDS)
                process.terminate()
                process.join(2.0)
            self._connection.close()
        self._snapshot = None
        self._frame = None
        self._status, self._detail = SourceStatus.DISABLED, ""

    def latest(self) -> VisionSnapshot | None:
        self._drain()
        return self._snapshot

    def latest_frame(self) -> bytes | None:
        return self._frame

    def take_sign_cycles(self) -> list[list[Sign]]:
        cycles, self._sign_cycles = self._sign_cycles, []
        return cycles

    @property
    def status(self) -> SourceStatus:
        return self._status

    @property
    def detail(self) -> str:
        return f"{self._detail} · {self._note}" if self._note else self._detail

    def _drain(self) -> None:
        """Read everything the camera process has sent since the last call. Never blocks."""
        process = self._process
        if process is None:
            return
        try:
            while self._connection.poll():
                self._handle(self._connection.recv())
        except (EOFError, OSError):
            pass  # handled by the liveness check below
        if process.is_alive():
            return
        if self._restart_at is None:
            logger.error("Camera process exited unexpectedly (exit code %s)", process.exitcode)
            self._snapshot = None
            self._frame = None
            self._status = SourceStatus.UNAVAILABLE
            if self._restarts >= MAX_PROCESS_RESTARTS:
                self._detail = "โปรเซสของกล้องหยุดทำงานซ้ำหลายครั้ง ต้องเปิดโปรแกรมใหม่"
                self._restart_at = float("inf")
            else:
                self._detail = "โปรเซสของกล้องหยุดทำงาน กำลังเริ่มใหม่"
                self._restart_at = time.monotonic() + self._config.retry_seconds
        elif time.monotonic() >= self._restart_at:
            self._restarts += 1
            logger.warning(
                "Restarting the camera process (%d of %d)", self._restarts, MAX_PROCESS_RESTARTS
            )
            self._process = None
            self._connection.close()
            self._restart_at = None
            self.start()

    def _handle(self, message: tuple) -> None:
        kind = message[0]
        if kind == oak_worker.MSG_FRAME:
            self._frame = message[1]
        elif kind == oak_worker.MSG_SIGNS:
            self._sign_cycles.append([Sign(sign_kind, code) for sign_kind, code in message[1]])
        elif kind == oak_worker.MSG_NOTE:
            logger.warning("Camera: %s (%s)", message[1], message[2])
            self._note = message[1]
        elif kind == oak_worker.MSG_PEOPLE:
            # (x, z, confidence, box[, y]): y was added later, so older tuples still parse.
            people = tuple(PersonObservation(*person) for person in message[1])
            self._snapshot = VisionSnapshot(time.monotonic(), people)
        elif kind == oak_worker.MSG_STATUS:
            _kind, status, detail, technical = message
            if status == oak_worker.STATUS_RUNNING:
                logger.info("Camera running: %s", detail)
                self._status = SourceStatus.RUNNING
            else:
                logger.warning("Camera unavailable: %s (%s)", detail, technical)
                self._status = SourceStatus.UNAVAILABLE
                self._snapshot = None
                self._frame = None
            self._detail = detail
