"""Is the person in front of the camera talking? Judged from how much their mouth moves.

Two halves, kept apart like ``vision/signs.py``:

* ``MouthDetector`` looks at one camera picture and says how far the nearest person's jaw is open (MediaPipe's face
  landmarker, the ``jawOpen`` value, 0 = closed ... 1 = wide open). It needs MediaPipe and OpenCV, so it runs in
  the camera process. At 1.5 - 3 m a face is only 45 - 90 pixels wide in the 640 x 480 picture, too small to find, so the
  person's head (the top of their box) is cut out and enlarged first. Measured on 9 Nov 2026 with a portrait made
  small: found down to a face 45 px wide when enlarged, about 9 ms per picture on the processor.
* ``MouthTracker`` keeps the readings and answers "did the mouth move between then and now?". Plain Python.

It is only ever evidence, and it says "I cannot tell" (None) whenever it has no face to look at: the caller must never
treat that as "nobody is talking".
"""
from __future__ import annotations

import logging
from collections import deque
from pathlib import Path

logger = logging.getLogger(__name__)

MOUTH_MODEL_FILE = "face_landmarker.task"
MOUTH_MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task"
)
HEAD_SHARE = 0.45  # the top of a person's box that holds the head (more when they are close and the box is cut off)
HEAD_PADDING = 0.20  # of the box's width, on each side
HEAD_MIN_SIDE = 360  # the cut-out is enlarged to at least this many pixels on its short side
KEEP_S = 8.0
MIN_FACE_READINGS = 3  # fewer than this in a stretch of time: no opinion
FACE_SEEN_WITHIN_S = 1.5


class MouthDetector:
    """``measure(jpeg, focus)``: how open the jaw of the person in ``focus`` is, or None if no face was found."""

    def __init__(self, model_dir: str) -> None:
        self._landmarker = None
        self.problem = ""  # why the mouth cannot be read, if it cannot
        try:
            self._landmarker = self._load(Path(model_dir))
        except Exception as exc:  # no internet for the first download, a broken install...
            self.problem = f"{type(exc).__name__}: {exc}"

    @property
    def ready(self) -> bool:
        return self._landmarker is not None

    @staticmethod
    def _load(model_dir: Path):
        import urllib.request

        from mediapipe.tasks import python as mp_python
        from mediapipe.tasks.python import vision

        path = model_dir / MOUTH_MODEL_FILE
        if not path.exists():
            model_dir.mkdir(parents=True, exist_ok=True)
            partial = path.with_suffix(".part")
            with urllib.request.urlopen(MOUTH_MODEL_URL, timeout=60) as response:
                partial.write_bytes(response.read())
            partial.replace(path)
        options = vision.FaceLandmarkerOptions(
            base_options=mp_python.BaseOptions(model_asset_path=str(path)),
            running_mode=vision.RunningMode.IMAGE,
            num_faces=1,
            output_face_blendshapes=True,
        )
        return vision.FaceLandmarker.create_from_options(options)

    def measure(self, jpeg: bytes, focus: tuple[float, float, float, float] | None) -> float | None:
        if self._landmarker is None or focus is None:
            return None
        import cv2
        import mediapipe as mp
        import numpy as np

        picture = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
        if picture is None:
            return None
        head = head_part(picture, focus, cv2)
        if head is None:
            return None
        rgb = np.ascontiguousarray(cv2.cvtColor(head, cv2.COLOR_BGR2RGB))
        result = self._landmarker.detect(mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb))
        if not result.face_blendshapes:
            return None
        for category in result.face_blendshapes[0]:
            if category.category_name == "jawOpen":
                return float(category.score)
        return None


def head_part(picture, box: tuple[float, float, float, float], cv2):
    """The head of the person in ``box`` (0-1 of the picture), enlarged; None if the box is useless."""
    height, width = picture.shape[:2]
    xmin, ymin, xmax, ymax = box
    box_w, box_h = (xmax - xmin) * width, (ymax - ymin) * height
    if box_w < 12 or box_h < 12:
        return None
    left = max(0, int(xmin * width - HEAD_PADDING * box_w))
    right = min(width, int(xmax * width + HEAD_PADDING * box_w))
    top = max(0, int(ymin * height - 0.05 * box_h))
    bottom = min(height, int(ymin * height + max(HEAD_SHARE * box_h, 0.8 * box_w)))
    part = picture[top:bottom, left:right]
    if part.size == 0:
        return None
    scale = HEAD_MIN_SIDE / max(1, min(part.shape[:2]))
    if scale > 1.0:
        part = cv2.resize(part, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    return part


class MouthTracker:
    """The readings of the last few seconds: ``(time, jaw opening or None)``; None = no face in that picture."""

    def __init__(self) -> None:
        self._readings: deque[tuple[float, float | None]] = deque()

    def add(self, when: float, value: float | None) -> None:
        self._readings.append((when, value))
        while self._readings and self._readings[0][0] < when - KEEP_S:
            self._readings.popleft()

    def clear(self) -> None:
        self._readings.clear()

    def face_seen(self, now: float, within_s: float = FACE_SEEN_WITHIN_S) -> bool:
        return any(value is not None and when >= now - within_s for when, value in self._readings)

    def activity(self, since: float, now: float, threshold: float) -> bool | None:
        """True: the mouth moved (the jaw opening varied by at least ``threshold``) between ``since`` and ``now``.
        False: a face was seen all that time and the mouth stayed still. None: not enough of a face seen to say."""
        values = [value for when, value in self._readings if since <= when <= now and value is not None]
        if len(values) < MIN_FACE_READINGS:
            return None
        return (max(values) - min(values)) >= threshold
