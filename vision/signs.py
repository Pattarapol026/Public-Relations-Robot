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
import time
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
    "Six_Seven": "ท่า 67 (สองมือขึ้นลงสลับกัน)",  # not a shape: two hands moving up and down in turn
    "Wave_Visitor_Right": "โบกมือขวา บ๊ายบาย (แบมือแกว่งซ้ายขวา)",  # the visitor's right hand: the left of the picture
    "Wave_Visitor_Left": "โบกมือซ้าย บ๊ายบาย (แบมือแกว่งซ้ายขวา)",
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


SIX_SEVEN_NAME = "Six_Seven"
WAVE_RIGHT_NAME = "Wave_Visitor_Right"
WAVE_LEFT_NAME = "Wave_Visitor_Left"
# Gestures that are movements: the detector has already watched them for a while, so they count at once.
MOTION_GESTURES = frozenset({SIX_SEVEN_NAME, WAVE_RIGHT_NAME, WAVE_LEFT_NAME})


@dataclass(frozen=True)
class Hand:
    """One hand in a picture: where (pixels), how big, whether it is open, and how wide the picture is."""

    x: float
    y: float
    palm: float
    open: bool
    width: float


class WaveDetector:
    """A visitor waving goodbye: an open hand swung from side to side.

    Plain Python, fed the hands of each picture. It follows the nearest open hand and counts strokes: a stroke is a
    sideways move of at least AMPLITUDE palm lengths, then a move back is the next. Three waves (each there and back) is
    MIN_STROKES strokes; it is on from the moment the last of them is made and for HOLD_S after the last stroke. The
    hand must move mostly sideways (a hand going up and down is not waving), and the side the hand is on, against the
    middle of the person, says which hand it is: the visitor's right hand is on the left of the picture.
    """

    WINDOW_S = 3.0
    AMPLITUDE = 0.9  # palm lengths
    MIN_STROKES = 5  # three waves: there, back, there, back, there
    HOLD_S = 1.0
    LOST_S = 0.8
    MOVING_S = 0.7  # after a quick sideways move, a hand is "moving" for this long: it is not showing an open palm
    QUICK = 0.35  # palm lengths between two pictures

    def __init__(self) -> None:
        self._reset()
        self.moving_until = float("-inf")
        self.side: str | None = None

    def _reset(self) -> None:
        self._direction = 0
        self._extreme = 0.0
        self._strokes: list[float] = []
        self._track: list[tuple[float, float, float]] = []  # (time, x, y) in palm lengths
        self._last_position: tuple[float, float] | None = None
        self._last_seen = float("-inf")

    def update(self, now: float, hands: list[Hand], centre: float = 0.5) -> bool:
        """True while a wave is on; ``side`` then says WAVE_RIGHT_NAME or WAVE_LEFT_NAME."""
        open_hands = [hand for hand in hands if hand.open and hand.palm > 1e-6]
        if not open_hands:
            if now - self._last_seen > self.LOST_S:
                self._reset()
            return self._on(now)
        if self._last_position is not None:
            near = min(open_hands, key=lambda h: abs(h.x - self._last_position[0]) + abs(h.y - self._last_position[1]))
            hand = near if abs(near.x - self._last_position[0]) < 4 * near.palm else max(open_hands, key=lambda h: h.palm)
        else:
            hand = max(open_hands, key=lambda h: h.palm)
        previous = self._last_position
        self._last_position, self._last_seen = (hand.x, hand.y), now
        x, y = hand.x / hand.palm, hand.y / hand.palm
        if previous is not None and abs(hand.x - previous[0]) / hand.palm >= self.QUICK:
            self.moving_until = now + self.MOVING_S
        self._track.append((now, x, y))
        self._track = [entry for entry in self._track if now - entry[0] <= self.WINDOW_S]
        self._stroke(now, x)
        self._strokes = [moment for moment in self._strokes if now - moment <= self.WINDOW_S]
        if self._on(now):
            xs = [entry[1] for entry in self._track]
            ys = [entry[2] for entry in self._track]
            if max(ys) - min(ys) > 1.2 * (max(xs) - min(xs)):
                return False  # mostly up and down
            side_of_picture = sum(h.x / h.width for h in [hand]) - centre
            self.side = WAVE_RIGHT_NAME if side_of_picture < 0 else WAVE_LEFT_NAME
            return True
        return False

    def _stroke(self, now: float, x: float) -> None:
        if self._direction == 0:
            if self._extreme == 0.0 and not self._strokes:
                self._extreme = x
            if abs(x - self._extreme) >= self.AMPLITUDE:
                self._direction = 1 if x > self._extreme else -1
                self._extreme = x
                self._strokes.append(now)
            return
        if (x - self._extreme) * self._direction > 0:
            self._extreme = x  # still going the same way
        elif abs(x - self._extreme) >= self.AMPLITUDE:
            self._direction = -self._direction
            self._extreme = x
            self._strokes.append(now)

    def _on(self, now: float) -> bool:
        return len(self._strokes) >= self.MIN_STROKES and now - self._strokes[-1] <= self.HOLD_S

    def moving(self, now: float) -> bool:
        return now < self.moving_until


class SixSevenDetector:
    """The "6 7" gesture: two hands held out and moved up and down in turn, one rising as the other falls.

    Plain Python, fed what the camera process saw in each picture: ``(x, y, palm)`` of every hand, in pixels. What it
    watches is the height of the left hand against the right one, in palm lengths: it must swing from one hand being
    clearly higher to the other being clearly higher, again and again (MIN_FLIPS times in WINDOW_S seconds).
    Lifting both hands together, or one hand waving, never counts.
    """

    WINDOW_S = 3.0
    MIN_FLIPS = 3
    LEVEL = 0.6  # palm lengths one hand must be above the other, either way, to count as a side
    MIN_APART = 1.2  # the hands must be at least this many palm lengths apart sideways
    ANTI_PHASE = 0.35  # the middle height of the two hands may move at most this much of how far apart they swing:
    # one hand moving against one that stays, or both rising together, is not two hands in turn
    HOLD_S = 1.2  # it stays on this long after the last swing, so the picture does not flicker
    LOST_S = 1.0  # no pair of hands for this long: start again

    def __init__(self) -> None:
        self._side = 0
        self._flips: list[float] = []
        self._heights: list[tuple[float, float, float]] = []  # (time, left - right, middle), in palm lengths
        self._last_pair = float("-inf")

    def update(self, now: float, hands: list[tuple[float, float, float]]) -> bool:
        pair = None
        if len(hands) >= 2:
            two = sorted(sorted(hands, key=lambda hand: -hand[2])[:2], key=lambda hand: hand[0])
            palm = (two[0][2] + two[1][2]) / 2
            if palm > 1e-6 and two[1][0] - two[0][0] >= self.MIN_APART * palm:
                pair = (two[0][1] - two[1][1]) / palm  # positive: the left hand is lower in the picture
                middle = (two[0][1] + two[1][1]) / 2 / palm
        if pair is None:
            if now - self._last_pair > self.LOST_S:
                self._side, self._flips, self._heights = 0, [], []
        else:
            self._last_pair = now
            self._heights.append((now, pair, middle))
            side = 1 if pair > self.LEVEL else -1 if pair < -self.LEVEL else self._side
            if side != self._side:
                if self._side != 0:
                    self._flips.append(now)
                self._side = side
        self._flips = [moment for moment in self._flips if now - moment <= self.WINDOW_S]
        self._heights = [entry for entry in self._heights if now - entry[0] <= self.WINDOW_S]
        if len(self._flips) < self.MIN_FLIPS or now - self._flips[-1] > self.HOLD_S or not self._heights:
            return False
        apart = [entry[1] for entry in self._heights]
        middle_moves = [entry[2] for entry in self._heights]
        return (max(middle_moves) - min(middle_moves)) <= self.ANTI_PHASE * (max(apart) - min(apart))


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
        self._six_seven = SixSevenDetector()
        self._wave = WaveDetector()
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
            hands, gestures, points = self._gestures(rgb)
            middle = 0.5 if focus is None else (focus[0] + focus[2]) / 2  # the person's middle, to tell left from right
            if not hands and focus is not None:
                closer = _person_part(rgb, focus, cv2)
                if closer is not None:
                    _hands, gestures, points = self._gestures(closer)
                    middle = 0.5  # the enlarged part is centred on the person
            now = time.monotonic()
            if self._six_seven.update(now, [(hand.x, hand.y, hand.palm) for hand in points]):
                gestures = [SIX_SEVEN_NAME]  # two hands swinging in turn: no other sign is meant (not "open palm")
            elif self._wave.update(now, points, middle):
                gestures = [self._wave.side]  # an open hand swinging: goodbye, not "open palm" (which means stop)
            elif self._wave.moving(now):
                gestures = [name for name in gestures if name != "Open_Palm"]  # a hand on its way is not showing a palm
            found += [(KIND_GESTURE, name) for name in gestures]
        return found

    def _gestures(self, rgb) -> tuple[int, list[str], list[Hand]]:
        """How many hands were seen, the gestures among them, and each hand (pixels)."""
        import mediapipe as mp

        height, width = rgb.shape[:2]
        result = self._recognizer.recognize(mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb))
        names: list[str] = []
        centres: list[Hand] = []
        for index, landmarks in enumerate(result.hand_landmarks):
            points = [(point.x * width, point.y * height) for point in landmarks]
            if len(points) == 21:
                palm = _distance(points[0], points[9])
                is_open = finger_states(points).count("extended") >= 3
                centres.append(Hand(sum(points[i][0] for i in (0, 5, 9, 13, 17)) / 5,
                                    sum(points[i][1] for i in (0, 5, 9, 13, 17)) / 5, palm, is_open, float(width)))
        for index, categories in enumerate(result.gestures):
            if not categories:
                continue
            landmarks = result.hand_landmarks[index] if index < len(result.hand_landmarks) else []
            points = [(point.x * width, point.y * height) for point in landmarks]
            name = choose_gesture(categories[0].category_name, categories[0].score, points)
            if name is not None and name not in names:
                names.append(name)
        return len(result.gestures), names, centres

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
            if sign.code in MOTION_GESTURES and now >= self._quiet_until:
                # Watched for a while by its own detector already: counts at once, as the third wave is made.
                self._reported.add(sign)
                confirmed.append(sign)
                self._quiet_until = now + config.gesture_quiet_s
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
