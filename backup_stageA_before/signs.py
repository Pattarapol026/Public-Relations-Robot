"""Recognising what a visitor shows the camera: marker cards and hand gestures.

Two halves, kept apart on purpose:

* ``SignDetector`` looks at one camera picture. It needs OpenCV and MediaPipe,
  so it lives in the camera process (``vision/oak_worker.py``) and is never
  imported by the GUI.
* ``SignTracker`` turns the stream of per-picture results into single events —
  "this card was shown" — and is plain Python.

Cards carry an ArUco marker (dictionary 4x4_50). In tests with synthetic camera
pictures a marker was still read at 50 px across, where a QR code needed 180.
"""
from __future__ import annotations

import logging
import math
import urllib.request
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

KIND_CARD = "card"
KIND_GESTURE = "gesture"

# MediaPipe's built-in gesture names, with the labels shown to the Admin.
GESTURE_LABELS = {
    "Thumb_Up": "ยกนิ้วโป้งขึ้น",
    "Thumb_Down": "คว่ำนิ้วโป้งลง",
    "Open_Palm": "แบมือ",
    "Victory": "ชูสองนิ้ว",
    "Pointing_Up": "ชี้นิ้วขึ้น",
    "Closed_Fist": "กำมือ",
    "ILoveYou": "สัญลักษณ์ไอเลิฟยู",
    "Finger_Gun": "ชี้นิ้วไปด้านข้าง ตั้งนิ้วโป้ง (รูปตัว L)",  # not one of MediaPipe's: told from the hand's shape
}
MIN_GESTURE_SCORE = 0.6
# The thumbs are checked against the hand's own landmarks, which tell up from down far
# better than the model's label does: with that check a less sure label is enough.
MIN_THUMB_SCORE = 0.4
# Where no hand is found in the whole picture, the nearest person is looked at closer:
# a hand 1.5 m away is only about 40 pixels wide in the 640 x 480 picture.
FOCUS_MIN_SIDE = 480  # the person's part of the picture is enlarged to at least this
GESTURE_MODEL_FILE = "gesture_recognizer.task"
GESTURE_MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/gesture_recognizer/"
    "gesture_recognizer/float16/1/gesture_recognizer.task"
)


@dataclass(frozen=True)
class Sign:
    kind: str  # KIND_CARD or KIND_GESTURE
    code: str  # card number as text, or the gesture name


class SignDetector:
    """Finds cards and gestures in one JPEG picture. Runs in the camera process."""

    def __init__(self, model_dir: str, gestures: bool = True) -> None:
        import cv2

        self._cv2 = cv2
        dictionary = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
        self._markers = cv2.aruco.ArucoDetector(dictionary, cv2.aruco.DetectorParameters())
        self._recognizer = None
        self.gesture_problem = ""  # why gestures are off, if they are
        if gestures:
            try:
                self._recognizer = self._load_gesture_model(Path(model_dir))
            except Exception as exc:  # no internet for the first download, broken install...
                self.gesture_problem = f"{type(exc).__name__}: {exc}"

    @property
    def gestures_ready(self) -> bool:
        return self._recognizer is not None

    def detect(self, jpeg: bytes, focus: tuple[float, float, float, float] | None = None) -> list[tuple[str, str]]:
        """Cards and gestures in the picture.

        ``focus`` is the nearest person's box (0-1 of the picture: xmin, ymin, xmax, ymax);
        if no hand is found in the whole picture, that part is looked at again, enlarged.
        """
        import numpy as np

        cv2 = self._cv2
        picture = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
        if picture is None:
            return []
        found: list[tuple[str, str]] = []
        _corners, ids, _rejected = self._markers.detectMarkers(
            cv2.cvtColor(picture, cv2.COLOR_BGR2GRAY)
        )
        if ids is not None:
            found += [(KIND_CARD, str(int(marker_id))) for marker_id in sorted(set(ids.flatten()))]

        if self._recognizer is not None:
            rgb = cv2.cvtColor(picture, cv2.COLOR_BGR2RGB)
            hands, gestures = self._gestures(rgb)
            if not hands and focus is not None:
                closer = _person_part(rgb, focus, cv2)
                if closer is not None:
                    _hands, gestures = self._gestures(closer)
            found += [(KIND_GESTURE, name) for name in gestures]
        return found

    def _gestures(self, rgb) -> tuple[int, list[str]]:
        """How many hands were seen, and the gestures among them."""
        import mediapipe as mp

        height, width = rgb.shape[:2]
        result = self._recognizer.recognize(mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb))
        names: list[str] = []
        for index, categories in enumerate(result.gestures):
            if not categories:
                continue
            landmarks = result.hand_landmarks[index] if index < len(result.hand_landmarks) else []
            points = [(point.x * width, point.y * height) for point in landmarks]
            name = choose_gesture(categories[0].category_name, categories[0].score, points)
            if name is not None and name not in names:
                names.append(name)
        return len(result.gestures), names

    @staticmethod
    def _load_gesture_model(model_dir: Path):
        from mediapipe.tasks import python as mp_python
        from mediapipe.tasks.python import vision

        model_path = model_dir / GESTURE_MODEL_FILE
        if not model_path.exists():
            model_dir.mkdir(parents=True, exist_ok=True)
            partial = model_path.with_suffix(".part")
            with urllib.request.urlopen(GESTURE_MODEL_URL, timeout=60) as response:
                partial.write_bytes(response.read())
            partial.replace(model_path)
        options = vision.GestureRecognizerOptions(
            base_options=mp_python.BaseOptions(model_asset_path=str(model_path)),
            running_mode=vision.RunningMode.IMAGE,
            num_hands=2,
        )
        return vision.GestureRecognizer.create_from_options(options)


def _person_part(rgb, box: tuple[float, float, float, float], cv2):
    """The person's upper body and the space around it (where hands are held up), enlarged."""
    height, width = rgb.shape[:2]
    xmin, ymin, xmax, ymax = box
    box_w, box_h = (xmax - xmin) * width, (ymax - ymin) * height
    if box_w < 8 or box_h < 8:
        return None
    left = max(0, int(xmin * width - 0.35 * box_w))
    right = min(width, int(xmax * width + 0.35 * box_w))
    top = max(0, int(ymin * height - 0.25 * box_h))
    bottom = min(height, int(ymin * height + 0.8 * box_h))
    part = rgb[top:bottom, left:right]
    if part.size == 0 or (right - left) * (bottom - top) > 0.6 * width * height:
        return None  # the person already fills the picture: nothing to gain
    scale = FOCUS_MIN_SIDE / max(1, min(part.shape[:2]))
    if scale > 1.0:
        part = cv2.resize(part, None, fx=scale, fy=scale, interpolation=cv2.INTER_LINEAR)
    import numpy as np

    return np.ascontiguousarray(part)


# --- the hand's shape, from MediaPipe's 21 hand landmarks ---
# 0 wrist; thumb 1-4; index 5-8; middle 9-12; ring 13-16; little 17-20 (base, middle joint, -, tip).
_FINGERS = ((5, 6, 8), (9, 10, 12), (13, 14, 16), (17, 18, 20))  # (base, middle joint, tip)
EXTENDED_RATIO = 1.15  # tip this much farther from the wrist than the middle joint: straight
FOLDED_RATIO = 1.0  # tip no farther from the wrist than the middle joint: curled in


def _distance(a: tuple[float, float], b: tuple[float, float]) -> float:
    return ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5


def finger_states(points: list[tuple[float, float]]) -> list[str]:
    """"extended", "folded" or "between" for the index, middle, ring and little finger."""
    wrist = points[0]
    states = []
    for _base, joint, tip in _FINGERS:
        ratio = _distance(wrist, points[tip]) / max(1e-6, _distance(wrist, points[joint]))
        if ratio >= EXTENDED_RATIO:
            states.append("extended")
        elif ratio <= FOLDED_RATIO:
            states.append("folded")
        else:
            states.append("between")
    return states


def thumb_direction(points: list[tuple[float, float]]) -> str | None:
    """"up" or "down" when the thumb sticks out clearly that way past the rest of the hand."""
    palm = _distance(points[0], points[9])
    if palm < 1e-6:
        return None
    base, tip = points[2], points[4]
    dx, dy = tip[0] - base[0], tip[1] - base[1]  # image y grows downwards
    if abs(dy) < 1.2 * abs(dx) or abs(dy) < 0.45 * palm:
        return None  # sideways, or too short to tell
    others = [points[i][1] for i in range(5, 21)]
    if dy < 0 and tip[1] < min(others) - 0.15 * palm:
        return "up"
    if dy > 0 and tip[1] > max(others) + 0.15 * palm:
        return "down"
    return None


FINGER_GUN_NAME = "Finger_Gun"
FINGER_GUN_MIN_ANGLE = 50.0  # degrees between the pointing finger and the thumb: an "L", not a fist or a flat hand
FINGER_GUN_MAX_ANGLE = 130.0
FINGER_GUN_THUMB_OUT = 0.5  # the thumb tip is at least this far (in palm lengths) from the base of the index finger


def is_finger_gun(points: list[tuple[float, float]]) -> bool:
    """The index finger straight out sideways, the thumb standing up from it, the other three fingers curled in.

    The same shape with either hand and pointing either way (the two pictures of it the team gave are mirror
    images). MediaPipe has no name for it, so it is judged from the 21 landmarks.
    """
    if len(points) != 21:
        return False
    index, middle, ring, little = finger_states(points)
    if index != "extended" or "extended" in (middle, ring, little):
        return False
    if [middle, ring, little].count("folded") < 2:
        return False  # fingers half open: a relaxed hand, not this sign
    palm = _distance(points[0], points[9])
    if palm < 1e-6:
        return False
    if _distance(points[4], points[5]) < FINGER_GUN_THUMB_OUT * palm:
        return False  # the thumb lies along the hand
    point_x, point_y = points[8][0] - points[5][0], points[8][1] - points[5][1]
    thumb_x, thumb_y = points[4][0] - points[2][0], points[4][1] - points[2][1]
    if abs(point_x) < 0.8 * abs(point_y):
        return False  # pointing up or down: that is "Pointing_Up", not this
    lengths = math.hypot(point_x, point_y) * math.hypot(thumb_x, thumb_y)
    if lengths < 1e-6:
        return False
    cosine = max(-1.0, min(1.0, (point_x * thumb_x + point_y * thumb_y) / lengths))
    return FINGER_GUN_MIN_ANGLE <= math.degrees(math.acos(cosine)) <= FINGER_GUN_MAX_ANGLE


def choose_gesture(name: str, score: float, points: list[tuple[float, float]]) -> str | None:
    """The gesture to count for one hand: the model's label, checked against the hand's shape.

    The model mixed up the two thumbs and took a thumb turning down for "I love you"
    (both seen in the log of 5 Nov). The landmarks settle which way the thumb points,
    and an "I love you" with the middle or ring finger straight, or the index or little
    finger curled, is not one.
    """
    if is_finger_gun(points):
        return FINGER_GUN_NAME  # whatever the model called it (usually "Pointing_Up" or nothing)
    sure = name in GESTURE_LABELS and score >= MIN_GESTURE_SCORE
    if len(points) != 21:
        return name if sure else None
    fingers = finger_states(points)
    is_thumb = name in ("Thumb_Up", "Thumb_Down")
    if "extended" not in fingers and (is_thumb and score >= MIN_THUMB_SCORE or not sure):
        # A thumb label, or no sure label at all, on a hand with its fingers curled:
        # the landmarks say which way the thumb points (a sure fist stays a fist).
        direction = thumb_direction(points)
        if direction is not None:
            return "Thumb_Up" if direction == "up" else "Thumb_Down"
    if not sure:
        return None
    if name == "ILoveYou":
        index, middle, ring, little = fingers
        if middle == "extended" or ring == "extended" or index == "folded" or little == "folded":
            # Not one; a thumb turning down was read this way.
            direction = thumb_direction(points) if "extended" not in fingers else None
            return {"up": "Thumb_Up", "down": "Thumb_Down"}.get(direction or "")
    return name


@dataclass(frozen=True)
class SignTrackerConfig:
    card_hold_s: float = 0.3  # a card must be seen this long before it counts
    gesture_hold_s: float = 0.7  # gestures need longer: hands pass through shapes by accident
    rearm_s: float = 1.5  # it must be gone this long before the same sign counts again
    gap_s: float = 0.6  # a sign missed for less than this is still "being shown"
    # A hand changing shape passes through others ("thumb down" -> "I love you" 0.24 s
    # apart in the log): a gesture counts only if it was most of what the hand showed
    # while it was held, and none other counts right after one did.
    gesture_share: float = 0.65  # of the pictures with a gesture in them, while holding
    gesture_quiet_s: float = 1.5  # after a gesture counts, other gestures wait this long


class SignTracker:
    """Reports each sign once per showing, however long it is held up."""

    def __init__(self, config: SignTrackerConfig = SignTrackerConfig()) -> None:
        self._config = config
        self._first_seen: dict[Sign, float] = {}
        self._last_seen: dict[Sign, float] = {}
        self._reported: set[Sign] = set()
        self._gesture_pictures: list[tuple[float, frozenset[Sign]]] = []  # pictures with a gesture
        self._quiet_until = 0.0

    def _share(self, sign: Sign, now: float) -> tuple[float, bool]:
        """Of recent pictures with a gesture, the part showing ``sign``, and whether it led."""
        window = [shown for seen, shown in self._gesture_pictures
                  if now - seen <= self._config.gesture_hold_s + 1e-9]
        if not window:
            return 0.0, False
        counts: dict[Sign, int] = {}
        for shown in window:
            for gesture in shown:
                counts[gesture] = counts.get(gesture, 0) + 1
        mine = counts.get(sign, 0)
        return mine / len(window), mine == max(counts.values())

    def update(self, signs: list[Sign], now: float) -> list[Sign]:
        config = self._config
        for sign in signs:
            # A sign still remembered (missed only briefly, or already reported
            # and not yet re-armed) continues the same showing.
            if sign not in self._last_seen:
                self._first_seen[sign] = now
            self._last_seen[sign] = now
        gestures = frozenset(sign for sign in signs if sign.kind == KIND_GESTURE)
        if gestures:
            self._gesture_pictures.append((now, gestures))
        horizon = now - config.gesture_hold_s - 1.0
        self._gesture_pictures = [entry for entry in self._gesture_pictures if entry[0] >= horizon]

        confirmed = []
        for sign in signs:
            if sign in self._reported:
                continue
            if sign.kind == KIND_CARD:
                if now - self._first_seen[sign] >= config.card_hold_s:
                    self._reported.add(sign)
                    confirmed.append(sign)
                continue
            if now - self._first_seen[sign] < config.gesture_hold_s or now < self._quiet_until:
                continue
            share, leads = self._share(sign, now)
            if share >= config.gesture_share and leads:
                self._reported.add(sign)
                confirmed.append(sign)
                self._quiet_until = now + config.gesture_quiet_s
                # The shapes the hand passed through on the way do not count later on.
                for other in [s for s in self._first_seen if s.kind == KIND_GESTURE and s != sign]:
                    if other not in self._reported:
                        self._first_seen[other] = now

        for sign in list(self._last_seen):
            gone_for = now - self._last_seen[sign]
            if sign in self._reported:
                if gone_for >= config.rearm_s:
                    self._forget(sign)
            elif gone_for > config.gap_s:
                self._forget(sign)  # never held long enough: start over next time
        return confirmed

    def _forget(self, sign: Sign) -> None:
        self._reported.discard(sign)
        self._first_seen.pop(sign, None)
        self._last_seen.pop(sign, None)
