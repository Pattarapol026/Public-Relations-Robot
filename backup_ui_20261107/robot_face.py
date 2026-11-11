"""The robot's animated face: fifteen expressions that follow what the robot is doing.

Drawn with QPainter in the 1920 x 1080 space of the reference artwork, then
scaled to the widget, so it stays sharp at any size and can move: a change of
expression fades the old face out and bounces the new one in, and each face keeps
a little life of its own (a gentle bob, blinking, a talking mouth, thinking dots
that appear one by one, a wobbling question mark, a sliding sweat drop, a
twinkling sparkle, slow breathing while idle, pulsing heart and star eyes, a falling
tear). The animation runs only while the face is on screen.

The last seven answer the visitor's hand gestures (see ``ui/main_window.py``).
"""
from __future__ import annotations

import math
import time

from PySide6.QtCore import QEasingCurve, QPointF, QRectF, Qt, QTimer, QVariantAnimation
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPaintEvent, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

MOOD_IDLE = "idle"  # nobody is using the robot
MOOD_NEUTRAL = "neutral"  # ready, or listening
MOOD_HAPPY = "happy"  # greeting a visitor
MOOD_SPEAKING = "speaking"
MOOD_THINKING = "thinking"
MOOD_CONFUSED = "confused"  # no answer, or the visitor said it was not right
MOOD_WORRIED = "worried"  # an error, a failed command, a safety stop
MOOD_DELIGHTED = "delighted"  # thanked, or a command done
# Answers to gestures.
MOOD_STARSTRUCK = "starstruck"  # thumbs up: "great!"
MOOD_LOVE = "love"  # the I-love-you sign
MOOD_SAD = "sad"  # thumbs down
MOOD_WINK = "wink"  # the V sign: posing for a photo
MOOD_CALM = "calm"  # open palm: stopped, eyes gently closed
MOOD_EXCITED = "excited"  # a raised fist: cheering
MOOD_CURIOUS = "curious"  # pointing up: a question is coming
MOODS = (MOOD_IDLE, MOOD_NEUTRAL, MOOD_HAPPY, MOOD_SPEAKING, MOOD_THINKING,
         MOOD_CONFUSED, MOOD_WORRIED, MOOD_DELIGHTED, MOOD_STARSTRUCK, MOOD_LOVE,
         MOOD_SAD, MOOD_WINK, MOOD_CALM, MOOD_EXCITED, MOOD_CURIOUS)

COLOR_BACKGROUND = QColor("#101a2d")
COLOR_EYE = QColor("#5ee6e0")
COLOR_MOUTH = QColor("#7df0cb")
COLOR_SHINE = QColor("#ffffff")
COLOR_BLUSH = QColor("#356a64")
COLOR_SWEAT = QColor("#80d4f6")
COLOR_SLEEP = QColor("#40aca6")
COLOR_HEART = QColor("#ff7aa8")
COLOR_STAR = QColor("#ffd166")

# The part of the 1920 x 1080 artwork the face occupies (with its question mark and dots).
VIEW = QRectF(320.0, 30.0, 1280.0, 960.0)  # centred on the face, wide enough for the sound waves
CORNER_RADIUS = 20.0
FRAME_MS = 33  # about 30 frames a second while visible
TRANSITION_MS = 450
BLINK_EVERY_S = 3.8
BLINK_S = 0.16


class RobotFace(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._mood = MOOD_IDLE
        self._previous: str | None = None
        self._progress = 1.0  # of the change from _previous to _mood
        self._started = time.monotonic()
        policy = QSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.setSizePolicy(policy)
        self.setMinimumHeight(120)
        self._transition = QVariantAnimation(self)
        self._transition.setStartValue(0.0)
        self._transition.setEndValue(1.0)
        self._transition.setDuration(TRANSITION_MS)
        self._transition.setEasingCurve(QEasingCurve.Type.Linear)  # each face eases on its own
        self._transition.valueChanged.connect(self._on_transition)
        self._transition.finished.connect(self._on_transition_done)
        self._ticker = QTimer(self)
        self._ticker.setInterval(FRAME_MS)
        self._ticker.timeout.connect(self.update)

    @property
    def mood(self) -> str:
        return self._mood

    def set_mood(self, mood: str) -> None:
        if mood not in MOODS:
            raise ValueError(f"unknown mood {mood!r}")
        if mood == self._mood:
            return
        self._previous, self._mood = self._mood, mood
        self._progress = 0.0
        self._transition.stop()
        self._transition.start()
        self.update()

    def sizeHint(self):
        hint = super().sizeHint()
        hint.setHeight(280)
        return hint

    # --- life ---

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._ticker.start()

    def hideEvent(self, event) -> None:
        super().hideEvent(event)
        self._ticker.stop()

    def _on_transition(self, value) -> None:
        self._progress = float(value)
        self.update()

    def _on_transition_done(self) -> None:
        self._progress = 1.0
        self._previous = None
        self.update()

    # --- painting ---

    def paintEvent(self, _event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        bounds = QRectF(self.rect())
        clip = QPainterPath()
        clip.addRoundedRect(bounds, CORNER_RADIUS, CORNER_RADIUS)
        painter.setClipPath(clip)
        painter.fillRect(bounds, COLOR_BACKGROUND)

        scale = min(bounds.width() / VIEW.width(), bounds.height() / VIEW.height())
        now = time.monotonic() - self._started
        p = self._progress
        if self._previous is not None and p < 1.0:
            out = _ease_in(p)
            self._draw(painter, bounds, scale, self._previous, now, 1.0 - out, 1.0 - 0.08 * out)
        come = _ease_out_back(p)
        self._draw(painter, bounds, scale, self._mood, now, min(1.0, p * 1.6), 0.9 + 0.1 * come)
        painter.end()

    def _draw(self, painter: QPainter, bounds: QRectF, scale: float, mood: str, now: float,
              opacity: float, grow: float) -> None:
        if opacity <= 0.0:
            return
        painter.save()
        painter.setOpacity(opacity)
        # Centre the artwork's view in the widget, then the face's own gentle motion.
        painter.translate(bounds.center())
        painter.scale(scale * grow, scale * grow)
        if mood in (MOOD_IDLE, MOOD_CALM):
            breath = 1.0 + 0.015 * math.sin(now * 1.4)
            painter.scale(breath, breath)
            bob = 3.0 * math.sin(now * 0.7)
        elif mood in (MOOD_HAPPY, MOOD_DELIGHTED, MOOD_STARSTRUCK, MOOD_LOVE, MOOD_WINK):
            bob = -10.0 * abs(math.sin(now * 3.0))  # a little hop
        elif mood == MOOD_EXCITED:
            bob = -16.0 * abs(math.sin(now * 5.0))  # bouncing
        elif mood == MOOD_SAD:
            bob = 12.0 + 4.0 * math.sin(now * 0.9)  # sunk a little, slow
        else:
            bob = 7.0 * math.sin(now * 1.8)
        painter.translate(-VIEW.center().x(), -VIEW.center().y() + bob)
        _FACES[mood](painter, now)
        painter.restore()


# --- the eight faces, in artwork coordinates ---


def _blink(now: float) -> float:
    """0 = eyes open, 1 = shut; a quick blink every few seconds."""
    phase = now % BLINK_EVERY_S
    if phase > BLINK_S:
        return 0.0
    return math.sin(phase / BLINK_S * math.pi)


def _round_eye(painter: QPainter, cx: float, cy: float, r: float, shine: tuple, blink: float) -> None:
    painter.save()
    painter.translate(cx, cy)
    painter.scale(1.0, max(0.08, 1.0 - blink))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(COLOR_EYE)
    painter.drawEllipse(QPointF(0, 0), r, r)
    if blink < 0.6:
        painter.setBrush(COLOR_SHINE)
        for dx, dy, size in shine:
            painter.drawEllipse(QPointF(dx, dy), size, size)
    painter.restore()


def _stroke(painter: QPainter, path: QPainterPath, color: QColor, width: float) -> None:
    pen = QPen(color, width)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawPath(path)


def _curve(x1: float, y1: float, x2: float, y2: float, depth_y: float) -> QPainterPath:
    """A curve from (x1, y1) to (x2, y2) whose middle reaches ``depth_y``."""
    path = QPainterPath(QPointF(x1, y1))
    control = 2 * depth_y - (y1 + y2) / 2
    path.quadTo(QPointF((x1 + x2) / 2, control), QPointF(x2, y2))
    return path


def _blush(painter: QPainter, y: float) -> None:
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(COLOR_BLUSH)
    for cx in (511.0, 1409.0):
        painter.drawEllipse(QPointF(cx, y), 82, 48)


def _smiling_eyes(painter: QPainter) -> None:
    for cx in (688.0, 1232.0):
        _stroke(painter, _curve(cx - 116, 513, cx + 115, 513, 417), COLOR_EYE, 46)


def _face_neutral(painter: QPainter, now: float) -> None:
    blink = _blink(now)
    shine = ((-40, -48, 41), (41, 74, 20))
    _round_eye(painter, 688, 438, 136, shine, blink)
    _round_eye(painter, 1232, 438, 136, shine, blink)
    _stroke(painter, _curve(845, 743, 1075, 743, 797), COLOR_MOUTH, 34)


def _face_happy(painter: QPainter, _now: float) -> None:
    _smiling_eyes(painter)
    mouth = QPainterPath(QPointF(812, 697))
    mouth.lineTo(1108, 697)
    mouth.quadTo(QPointF(960, 953), QPointF(812, 697))
    painter.setPen(QPen(COLOR_MOUTH, 16, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    painter.setBrush(COLOR_MOUTH)
    painter.drawPath(mouth)


def _face_speaking(painter: QPainter, now: float) -> None:
    blink = _blink(now)
    shine = ((-49, -57, 49), (48, 89, 25))
    _round_eye(painter, 688, 431, 163, shine, blink)
    _round_eye(painter, 1232, 431, 163, shine, blink)
    _blush(painter, 648)
    talk = 0.5 + 0.5 * abs(math.sin(now * 9.0))  # the mouth opens and closes with the words
    _stroke(painter, _curve(862, 743, 1057, 743, 743 + 47 * (0.5 + talk)), COLOR_MOUTH, 32)
    centre = QPointF(1300, 471)
    for index, (radius, sweep) in enumerate(((154, 38), (271, 41))):
        pulse = 0.35 + 0.65 * (0.5 + 0.5 * math.sin(now * 5.0 - index * 1.2))
        painter.save()
        painter.setOpacity(painter.opacity() * pulse)
        pen = QPen(COLOR_MOUTH, 32)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        rect = QRectF(centre.x() - radius, centre.y() - radius, 2 * radius, 2 * radius)
        painter.drawArc(rect, int(-sweep * 16), int(2 * sweep * 16))
        painter.restore()


def _face_thinking(painter: QPainter, now: float) -> None:
    blink = _blink(now)
    glance = 6.0 * math.sin(now * 0.9)  # eyes drift a little while thinking
    shine = ((41 + glance, -54, 48), (-28 + glance, 54, 21))
    _round_eye(painter, 688, 417, 129, shine, blink)
    _round_eye(painter, 1232, 417, 129, shine, blink)
    _blush(painter, 648)
    _stroke(painter, _curve(890, 757, 1057, 743, 790), COLOR_MOUTH, 34)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(COLOR_MOUTH)
    shown = int(now / 0.4) % 5  # 0..3 dots appear in turn, then a short pause
    for index, (x, y, r) in enumerate(((1313, 254, 20), (1388, 186, 34), (1490, 111, 48))):
        if index < shown:
            painter.drawEllipse(QPointF(x, y), r, r)


def _face_confused(painter: QPainter, now: float) -> None:
    blink = _blink(now + 1.3)
    shine = ((-45, -52, 45), (45, 82, 23))
    _round_eye(painter, 688, 458, 150, shine, blink)
    _round_eye(painter, 1232, 403, 150, shine, blink)
    _blush(painter, 648)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(COLOR_MOUTH)
    painter.drawEllipse(QPointF(960, 771), 48, 41)
    painter.save()
    painter.translate(1458, 185)
    painter.rotate(10.0 * math.sin(now * 2.5))
    font = QFont(painter.font())
    font.setPixelSize(300)
    font.setBold(True)
    painter.setFont(font)
    painter.setPen(COLOR_MOUTH)
    painter.drawText(QRectF(-150, -170, 300, 340), Qt.AlignmentFlag.AlignCenter, "?")
    painter.restore()


def _face_worried(painter: QPainter, now: float) -> None:
    blink = _blink(now + 0.7)
    shine = ((-37, -43, 37), (37, 67, 19))
    _round_eye(painter, 688, 485, 123, shine, blink)
    _round_eye(painter, 1232, 485, 123, shine, blink)
    left = QPainterPath(QPointF(551, 335))
    left.lineTo(797, 253)
    right = QPainterPath(QPointF(1123, 253))
    right.lineTo(1368, 335)
    _stroke(painter, left, COLOR_EYE, 40)
    _stroke(painter, right, COLOR_EYE, 40)
    _stroke(painter, _curve(838, 812, 1082, 812, 757), COLOR_MOUTH, 34)
    slide = (now % 2.0) / 2.0  # the drop runs down, fades, and comes back
    painter.save()
    painter.setOpacity(painter.opacity() * (1.0 - 0.7 * slide))
    painter.translate(0, 40 * slide)
    drop = QPainterPath(QPointF(1450, 200))
    drop.cubicTo(QPointF(1478, 250), QPointF(1484, 290), QPointF(1484, 325))
    drop.arcTo(QRectF(1416, 291, 68, 68), 0, -180)
    drop.cubicTo(QPointF(1416, 290), QPointF(1422, 250), QPointF(1450, 200))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(COLOR_SWEAT)
    painter.drawPath(drop)
    painter.restore()


def _face_delighted(painter: QPainter, now: float) -> None:
    _smiling_eyes(painter)
    _blush(painter, 676)
    _stroke(painter, _curve(796, 730, 1124, 730, 815), COLOR_MOUTH, 40)
    painter.save()
    painter.translate(1449, 213)
    painter.rotate(15.0 * math.sin(now * 2.0))
    twinkle = 0.85 + 0.25 * math.sin(now * 4.0)
    painter.scale(twinkle, twinkle)
    star = QPainterPath()
    for index in range(8):
        angle = math.radians(index * 45 - 90)
        radius = 82 if index % 2 == 0 else 22
        point = QPointF(radius * math.cos(angle), radius * math.sin(angle))
        if index == 0:
            star.moveTo(point)
        else:
            star.lineTo(point)
    star.closeSubpath()
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(COLOR_MOUTH)
    painter.drawPath(star)
    painter.restore()


def _face_idle(painter: QPainter, _now: float) -> None:
    for cx in (688.0, 1232.0):
        line = QPainterPath(QPointF(cx - 109, 472))
        line.lineTo(cx + 109, 472)
        _stroke(painter, line, COLOR_SLEEP, 48)
    mouth = QPainterPath(QPointF(878, 757))
    mouth.lineTo(1042, 757)
    _stroke(painter, mouth, COLOR_SLEEP, 28)


# --- the faces that answer gestures ---


def _star(points: int, outer: float, inner: float) -> QPainterPath:
    path = QPainterPath()
    for index in range(points * 2):
        angle = math.radians(index * 180.0 / points - 90)
        radius = outer if index % 2 == 0 else inner
        point = QPointF(radius * math.cos(angle), radius * math.sin(angle))
        if index == 0:
            path.moveTo(point)
        else:
            path.lineTo(point)
    path.closeSubpath()
    return path


def _heart(size: float) -> QPainterPath:
    path = QPainterPath(QPointF(0, 0.6 * size))
    path.cubicTo(QPointF(-1.1 * size, -0.1 * size), QPointF(-0.55 * size, -0.95 * size), QPointF(0, -0.4 * size))
    path.cubicTo(QPointF(0.55 * size, -0.95 * size), QPointF(1.1 * size, -0.1 * size), QPointF(0, 0.6 * size))
    return path


def _fill(painter: QPainter, path: QPainterPath, color: QColor, x: float, y: float,
          scale: float = 1.0, angle: float = 0.0) -> None:
    painter.save()
    painter.translate(x, y)
    painter.rotate(angle)
    painter.scale(scale, scale)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(color)
    painter.drawPath(path)
    painter.restore()


def _open_smile(painter: QPainter, top: float = 697, bottom: float = 953) -> None:
    mouth = QPainterPath(QPointF(812, top))
    mouth.lineTo(1108, top)
    mouth.quadTo(QPointF(960, bottom), QPointF(812, top))
    painter.setPen(QPen(COLOR_MOUTH, 16, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    painter.setBrush(COLOR_MOUTH)
    painter.drawPath(mouth)


def _face_starstruck(painter: QPainter, now: float) -> None:
    """Thumbs up: star eyes that pulse and turn a little, a big smile, sparkles."""
    pulse = 1.0 + 0.08 * math.sin(now * 6.0)
    star = _star(5, 165, 72)
    for cx, turn in ((688.0, 1.0), (1232.0, -1.0)):
        _fill(painter, star, COLOR_STAR, cx, 440, pulse, turn * 8.0 * math.sin(now * 2.0))
    _blush(painter, 660)
    _open_smile(painter)
    sparkle = _star(4, 46, 12)
    for index, (x, y) in enumerate(((420, 210), (1500, 190), (1530, 700))):
        twinkle = 0.5 + 0.5 * math.sin(now * 4.0 + index * 2.1)
        _fill(painter, sparkle, COLOR_MOUTH, x, y, 0.5 + 0.6 * twinkle)


def _face_love(painter: QPainter, now: float) -> None:
    """The I-love-you sign: heart eyes beating, small hearts floating up."""
    beat = 1.0 + 0.1 * abs(math.sin(now * 4.0))
    heart = _heart(170)
    for cx in (688.0, 1232.0):
        _fill(painter, heart, COLOR_HEART, cx, 440, beat)
    _blush(painter, 660)
    _stroke(painter, _curve(820, 735, 1100, 735, 830), COLOR_MOUTH, 40)
    small = _heart(48)
    for index in range(3):
        rise = (now * 0.45 + index / 3.0) % 1.0  # 0 -> 1, each heart in turn
        painter.save()
        painter.setOpacity(painter.opacity() * (1.0 - rise))
        _fill(painter, small, COLOR_HEART, 1450 + 40 * math.sin(rise * 6.0 + index), 520 - 420 * rise,
              0.7 + 0.5 * rise)
        painter.restore()


def _face_sad(painter: QPainter, now: float) -> None:
    """Thumbs down: drooping eyelids, a frown, a tear running down."""
    blink = _blink(now + 2.1)
    shine = ((-35, -20, 34), (35, 62, 17))
    for cx, outward in ((688.0, -1.0), (1232.0, 1.0)):
        _round_eye(painter, cx, 470, 120, shine, blink)
        lid = QPainterPath(QPointF(cx - 150, 300))  # hides the top of the eye, lower at the outer corner
        lid.lineTo(cx + 150, 300)
        lid.lineTo(cx + 150, 420 + 45 * outward)
        lid.lineTo(cx - 150, 420 - 45 * outward)
        lid.closeSubpath()
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(COLOR_BACKGROUND)
        painter.drawPath(lid)
    _stroke(painter, _curve(845, 820, 1075, 820, 765), COLOR_MOUTH, 34)
    fall = (now % 2.4) / 2.4
    painter.save()
    painter.setOpacity(painter.opacity() * (1.0 - fall))
    drop = QPainterPath(QPointF(0, -40))
    drop.cubicTo(QPointF(22, -5), QPointF(30, 15), QPointF(30, 30))
    drop.arcTo(QRectF(-30, 0, 60, 60), 0, -180)
    drop.cubicTo(QPointF(-30, 15), QPointF(-22, -5), QPointF(0, -40))
    _fill(painter, drop, COLOR_SWEAT, 610, 600 + 220 * fall)
    painter.restore()


def _face_wink(painter: QPainter, now: float) -> None:
    """The V sign: a wink and a big smile for the photo."""
    _stroke(painter, _curve(688 - 116, 470, 688 + 115, 470, 400), COLOR_EYE, 46)
    _round_eye(painter, 1232, 438, 140, ((-42, -50, 42), (42, 76, 21)), 0.0)
    _blush(painter, 660)
    _stroke(painter, _curve(800, 720, 1120, 720, 820), COLOR_MOUTH, 40)
    twinkle = 0.85 + 0.25 * math.sin(now * 4.0)
    _fill(painter, _star(4, 80, 20), COLOR_STAR, 1460, 230, twinkle, 20.0 * math.sin(now * 1.5))


def _face_calm(painter: QPainter, _now: float) -> None:
    """Open palm: stopped, eyes gently closed, a small smile."""
    for cx in (688.0, 1232.0):
        _stroke(painter, _curve(cx - 110, 450, cx + 110, 450, 500), COLOR_EYE, 40)
    _blush(painter, 640)
    _stroke(painter, _curve(880, 745, 1040, 745, 780), COLOR_MOUTH, 30)


def _face_excited(painter: QPainter, now: float) -> None:
    """A raised fist: squeezed eyes pointing in, a wide-open mouth, lines of energy."""
    for cx, side in ((688.0, 1.0), (1232.0, -1.0)):
        chevron = QPainterPath(QPointF(cx - 90 * side, 340))
        chevron.lineTo(cx + 80 * side, 440)
        chevron.lineTo(cx - 90 * side, 540)
        _stroke(painter, chevron, COLOR_EYE, 46)
    _open_smile(painter, 690, 990)
    flash = 0.4 + 0.6 * abs(math.sin(now * 5.0))
    painter.save()
    painter.setOpacity(painter.opacity() * flash)
    for side in (-1.0, 1.0):
        start = 960 + side * 560
        for dy, length in ((-90, 70), (0, 95), (90, 70)):
            line = QPainterPath(QPointF(start, 440 + dy))
            line.lineTo(start + side * length, 440 + dy * 1.4)
            _stroke(painter, line, COLOR_MOUTH, 22)
    painter.restore()


def _face_curious(painter: QPainter, now: float) -> None:
    """Pointing up: looking up, one eyebrow raised, an exclamation mark."""
    blink = _blink(now + 0.4)
    shine = ((-20, -78, 46), (40, 30, 20))  # the light high in the eye: looking up
    _round_eye(painter, 688, 450, 132, shine, blink)
    _round_eye(painter, 1232, 450, 132, shine, blink)
    left = QPainterPath(QPointF(580, 270))
    left.lineTo(790, 270)
    _stroke(painter, left, COLOR_EYE, 36)
    _stroke(painter, _curve(1120, 240, 1340, 240, 180), COLOR_EYE, 36)  # raised
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(COLOR_MOUTH)
    painter.drawEllipse(QPointF(960, 775), 40, 46)
    painter.save()
    painter.translate(1470, 200 - 14 * abs(math.sin(now * 3.0)))
    font = QFont(painter.font())
    font.setPixelSize(280)
    font.setBold(True)
    painter.setFont(font)
    painter.setPen(COLOR_STAR)
    painter.drawText(QRectF(-120, -160, 240, 320), Qt.AlignmentFlag.AlignCenter, "!")
    painter.restore()


_FACES = {
    MOOD_IDLE: _face_idle,
    MOOD_NEUTRAL: _face_neutral,
    MOOD_HAPPY: _face_happy,
    MOOD_SPEAKING: _face_speaking,
    MOOD_THINKING: _face_thinking,
    MOOD_CONFUSED: _face_confused,
    MOOD_WORRIED: _face_worried,
    MOOD_DELIGHTED: _face_delighted,
    MOOD_STARSTRUCK: _face_starstruck,
    MOOD_LOVE: _face_love,
    MOOD_SAD: _face_sad,
    MOOD_WINK: _face_wink,
    MOOD_CALM: _face_calm,
    MOOD_EXCITED: _face_excited,
    MOOD_CURIOUS: _face_curious,
}


def _ease_in(p: float) -> float:
    return p * p


def _ease_out_back(p: float) -> float:
    c1 = 1.70158
    c3 = c1 + 1.0
    return 1.0 + c3 * (p - 1.0) ** 3 + c1 * (p - 1.0) ** 2
