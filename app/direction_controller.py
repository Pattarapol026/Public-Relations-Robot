"""Listening to the visitor's direction: settings, calibration, the listening beam, the end-of-turn gate.

Everything here is off until an Admin has calibrated the microphone (where its front is) and
chosen a mode. Until then the robot listens exactly as it always did: this module changes
nothing about how a turn starts, ends or is answered. That is deliberate — an uncalibrated
direction would make the robot ignore the very people it should answer.

Modes (the doc "ฟังเฉพาะคนที่ยืนคุยด้วย", steps 1 and 2):
  off     the board turns by itself to the loudest voice (its default)
  front   the board listens only straight ahead of the camera
  follow  the board listens towards the visitor the camera has chosen (front if there is none)
Step 3, ending the turn by direction, is a separate switch: see ``blocks_speech``.
"""
from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass, replace

from PySide6.QtCore import QObject, QTimer, Signal

from app.voice_controller import VoiceController
from audio.direction import angle_between, wrap_degrees
from audio.xvf3800 import BoardSnapshot, Orientation, Xvf3800DirectionSource
from database.repositories import SettingsRepository
from services.speaker_matching import MountGeometry, person_azimuth

logger = logging.getLogger(__name__)

MODES = ("off", "front", "follow")
MODE_LABELS = {
    "off": "ปิด (บอร์ดหันหาเสียงดังที่สุดเอง)",
    "front": "ล็อกด้านหน้ากล้อง",
    "follow": "ตามผู้ใช้ที่กล้องเลือก",
}
KEY_PREFIX = "direction."
TICK_MS = 200  # at most 5 beam updates a second (doc 4.3)
CAPTURE_TICK_MS = 50  # while measuring for calibration
BEAM_MIN_CHANGE_DEG = 10.0
VOICE_STALE_S = 0.6  # a board verdict older than this says nothing about now
ANCHOR_READINGS = 6  # about 0.6 s of the visitor's speech found the direction of the turn
ANCHOR_MAX_SPREAD_DEG = 25.0
ANCHOR_FOREIGN_RUN = 4  # readings of speech from elsewhere, in a row, before it counts as someone else
ANCHOR_FOLLOW = 0.15  # how fast the anchor moves towards the visitor's own readings
ANCHORED_MIN_TOLERANCE_DEG = 35.0  # without calibration the direction is only as good as the board's reading
CAPTURE_SECONDS = 3.0
CAPTURE_MIN_READINGS = 8  # about 0.8 s of speech at 10 readings a second
CAPTURE_MAX_SPREAD_DEG = 15.0
SIGN_MIN_TURN_DEG = 15.0
RETRY_LOCK_S = 3.0
MIN_TOLERANCE_DEG = 10.0
MAX_TOLERANCE_DEG = 90.0


class DirectionError(Exception):
    """Invalid values; the message is safe to show to the Admin."""


@dataclass(frozen=True)
class DirectionSettings:
    mode: str = "off"
    front_deg: float = 0.0  # the board's angle for a person straight ahead of the camera
    front_known: bool = False
    sign: int = 1  # +1 when the board's angle grows towards the right
    sign_known: bool = False
    tolerance_deg: float = 30.0
    # Step 3. Needs no calibration: it follows the voice that started the turn (see ``blocks_speech``).
    end_turn: bool = True

    @property
    def orientation(self) -> Orientation:
        return Orientation(self.front_deg, self.sign)


@dataclass(frozen=True)
class CaptureResult:
    ok: bool
    message: str
    board_degrees: float | None = None
    spread_deg: float = 0.0
    readings: int = 0


class DirectionController(QObject):
    changed = Signal()  # settings or calibration changed
    board_changed = Signal()  # a new reading from the board (for the live dial)
    capture_progress = Signal(str)
    capture_finished = Signal(object)  # CaptureResult

    def __init__(
        self,
        source: Xvf3800DirectionSource,
        store: SettingsRepository,
        voice: VoiceController,
        vision,
        geometry: MountGeometry,
        is_robot_speaking=lambda: False,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._source = source
        self._store = store
        self._voice = voice
        self._vision = vision
        self._geometry = geometry
        self._is_robot_speaking = is_robot_speaking
        self._settings = self._load()
        source.set_orientation(self._settings.orientation)
        self._applied: tuple[bool, float | None] = (False, None)  # (asked for a lock, board angle)
        self._asked_at = 0.0
        self._last_snapshot_time = 0.0
        self._target_mic: float = 0.0

        # The voice that started the current turn (the board's own angle), found without any calibration.
        self._turn_started_at = 0.0
        self._anchor: float | None = None
        self._anchor_seen_at = 0.0  # time of the newest reading already used to follow the visitor

        self._capture_kind: str | None = None
        self._capture_started = 0.0
        self._capture_seen = 0.0
        self._capture_values: list[float] = []

        self._timer = QTimer(self)
        self._timer.setInterval(TICK_MS)
        self._timer.timeout.connect(self._tick)
        self._timer.start()

    # --- settings ---

    @property
    def settings(self) -> DirectionSettings:
        return self._settings

    @property
    def source(self) -> Xvf3800DirectionSource:
        return self._source

    def snapshot(self) -> BoardSnapshot:
        return self._source.snapshot()

    def _load(self) -> DirectionSettings:
        raw = {key[len(KEY_PREFIX):]: value for key, value in self._store.all().items()
               if key.startswith(KEY_PREFIX)}
        default = DirectionSettings()

        def number(name: str, fallback: float) -> float:
            try:
                value = float(raw.get(name, fallback))
            except (TypeError, ValueError):
                return fallback
            return value if math.isfinite(value) else fallback

        mode = raw.get("mode", default.mode)
        sign = -1 if raw.get("sign") == "-1" else 1
        return DirectionSettings(
            mode=mode if mode in MODES else default.mode,
            front_deg=number("front_deg", default.front_deg) % 360.0,
            front_known=raw.get("front_known") == "true",
            sign=sign,
            sign_known=raw.get("sign_known") == "true",
            tolerance_deg=min(MAX_TOLERANCE_DEG, max(MIN_TOLERANCE_DEG, number("tolerance_deg", default.tolerance_deg))),
            end_turn=raw.get("end_turn", "true") != "false",
        )

    def save(self, settings: DirectionSettings) -> None:
        if settings.mode not in MODES:
            raise DirectionError("โหมดทิศทางเสียงไม่ถูกต้อง")
        if not MIN_TOLERANCE_DEG <= settings.tolerance_deg <= MAX_TOLERANCE_DEG:
            raise DirectionError(
                f"ช่วงยอมรับต้องอยู่ระหว่าง ±{MIN_TOLERANCE_DEG:.0f}° ถึง ±{MAX_TOLERANCE_DEG:.0f}°"
            )
        if settings.mode != "off" and not settings.front_known:
            raise DirectionError("ต้องปรับเทียบทิศหน้าของไมค์ก่อน จึงจะเปิดโหมดนี้ได้")
        self._write(settings)
        logger.info(
            "Direction settings: mode=%s front=%.0f sign=%+d tolerance=%.0f end_turn=%s",
            settings.mode, settings.front_deg, settings.sign, settings.tolerance_deg, settings.end_turn,
        )

    def _write(self, settings: DirectionSettings) -> None:
        values = {
            "mode": settings.mode,
            "front_deg": f"{settings.front_deg:.1f}",
            "front_known": "true" if settings.front_known else "false",
            "sign": str(settings.sign),
            "sign_known": "true" if settings.sign_known else "false",
            "tolerance_deg": f"{settings.tolerance_deg:.0f}",
            "end_turn": "true" if settings.end_turn else "false",
        }
        for name, value in values.items():
            self._store.set(KEY_PREFIX + name, value)
        self._settings = settings
        self._source.set_orientation(settings.orientation)
        self.changed.emit()
        self._tick()

    def forget_calibration(self) -> None:
        """Back to the uncalibrated state: nothing listens by direction any more."""
        self._write(replace(
            self._settings, mode="off", front_known=False, sign_known=False, front_deg=0.0, sign=1,
        ))

    # --- what is in force ---

    @property
    def in_force(self) -> bool:
        """Direction may influence listening: calibrated, switched on, and the board is there."""
        s = self._settings
        return s.front_known and s.mode != "off" and self._source.snapshot().connected

    @property
    def target_mic_degrees(self) -> float:
        """Where the visitor is, in the microphone's frame (0 = straight ahead)."""
        return self._target_mic

    def utterance_veto_enabled(self) -> bool:
        return self.in_force

    def begin_turn(self) -> None:
        """A turn starts listening: forget whose voice the last one was."""
        self._turn_started_at = time.monotonic()
        self._anchor = None
        self._anchor_seen_at = 0.0

    def blocks_speech(self, now: float | None = None) -> bool:
        """True while the board hears speech that is not the visitor's: loudness that must not hold the turn open.

        Only used to end a turn sooner (step 3): it never stops a turn from starting, and it says False
        whenever it cannot tell, so the robot then listens exactly as it always did.

        Calibrated and switched on (mode front / follow): the visitor is where the camera says. Otherwise the
        visitor is the voice that began the turn: the first few readings of speech make an anchor (the
        board's own angle, no calibration), and speech that keeps coming from elsewhere is "someone else".
        One stray reading proves nothing, so it takes ``ANCHOR_FOREIGN_RUN`` in a row; the anchor follows the
        visitor's own readings slowly, so a person who shifts a little is not cut off.
        """
        s = self._settings
        if not s.end_turn or not self._source.snapshot().connected:
            return False
        now = time.monotonic() if now is None else now
        if self.in_force:
            last = self._source.last_voice()
            if last is None or now - last[0] > VOICE_STALE_S:
                return False
            return angle_between(last[1], self._target_mic) > s.tolerance_deg
        return self._foreign_voice(now)

    def _foreign_voice(self, now: float) -> bool:
        voices = self._source.voice_since(self._turn_started_at)
        if self._anchor is None:
            if len(voices) < ANCHOR_READINGS:
                return False  # not enough of the visitor's voice yet to know where it is
            mean, spread = circular_mean_degrees([angle for _t, angle in voices[:ANCHOR_READINGS]])
            if spread > ANCHOR_MAX_SPREAD_DEG:
                return False  # the first readings disagree: do not pick a side
            self._anchor = mean
            self._anchor_seen_at = voices[ANCHOR_READINGS - 1][0]
        tolerance = max(self._settings.tolerance_deg, ANCHORED_MIN_TOLERANCE_DEG)
        # Follow the visitor: each new reading near the anchor pulls it a little towards itself, once.
        for t, angle in voices:
            if t > self._anchor_seen_at:
                self._anchor_seen_at = t
                if angle_between(angle, self._anchor) <= tolerance:
                    self._anchor = (self._anchor + ANCHOR_FOLLOW * wrap_degrees(angle - self._anchor)) % 360.0
        recent = [angle for t, angle in voices if now - t <= VOICE_STALE_S]
        last = recent[-ANCHOR_FOREIGN_RUN:]
        return len(last) >= ANCHOR_FOREIGN_RUN and all(angle_between(a, self._anchor) > tolerance for a in last)

    # --- the listening beam ---

    def _desired_target(self) -> float:
        """The visitor's direction in the microphone's frame."""
        s = self._settings
        if s.mode == "follow" and s.sign_known:
            people = [p for p in self._vision.people
                      if p.measured and self._vision.is_at_conversation_distance(p)]
            if people:
                nearest = min(people, key=lambda p: p.z_m)
                return person_azimuth(nearest, self._geometry)
        return 0.0

    def _tick(self) -> None:
        snap = self._source.snapshot()
        if snap.timestamp != self._last_snapshot_time:
            self._last_snapshot_time = snap.timestamp
            self.board_changed.emit()
        self._advance_capture(snap)
        self._steer(snap)

    def _steer(self, snap: BoardSnapshot) -> None:
        s = self._settings
        want_lock = s.front_known and s.mode != "off" and snap.connected
        if not want_lock:
            if snap.connected and (snap.beam_locked or self._applied[0]):
                self._source.request_beam(None)
            self._applied = (False, None)
            self._target_mic = 0.0
            return
        target = self._desired_target()
        board = s.orientation.to_board_frame(target)
        now = time.monotonic()
        if snap.beam_locked and self._applied[1] is not None:
            if angle_between(board, self._applied[1]) < BEAM_MIN_CHANGE_DEG:
                self._target_mic = target
                return
            if self._voice.speech_active:
                return  # never turn the beam under someone who is speaking (doc 4.3)
        elif self._applied[0] and now - self._asked_at < RETRY_LOCK_S:
            return  # asked a moment ago and the board has not confirmed (or refused): do not nag
        self._target_mic = target
        self._applied = (True, board)
        self._asked_at = now
        self._source.request_beam(board)

    def shutdown(self) -> None:
        self._timer.stop()
        if self._source.snapshot().beam_locked:
            self._source.request_beam(None)

    # --- calibration (doc 4.5) ---

    @property
    def capturing(self) -> bool:
        return self._capture_kind is not None

    def start_capture(self, kind: str) -> None:
        """``kind``: "front" (stand straight ahead of the camera) or "right" (about 45° to its right)."""
        if kind not in ("front", "right"):
            raise DirectionError("ไม่รู้จักขั้นตอนปรับเทียบ")
        if self._capture_kind is not None:
            raise DirectionError("กำลังวัดอยู่ รอให้เสร็จก่อน")
        if not self._source.snapshot().connected:
            raise DirectionError("ยังไม่ได้เชื่อมต่อบอร์ด XVF3800 จึงวัดทิศไม่ได้")
        if kind == "right" and not self._settings.front_known:
            raise DirectionError("วัดทิศหน้าก่อน แล้วค่อยวัดทิศขวา")
        self._capture_kind = kind
        self._capture_started = time.monotonic()
        self._capture_seen = self._source.snapshot().timestamp
        self._capture_values = []
        self._timer.setInterval(CAPTURE_TICK_MS)  # sample the board as often as it speaks
        self.capture_progress.emit("กำลังฟัง… พูดต่อเนื่องใกล้ ๆ ตำแหน่งที่ยืน (เช่น นับ 1 ถึง 10)")

    def cancel_capture(self) -> None:
        self._capture_kind = None
        self._timer.setInterval(TICK_MS)

    def _advance_capture(self, snap: BoardSnapshot) -> None:
        if self._capture_kind is None:
            return
        if snap.timestamp > self._capture_seen and snap.board_degrees is not None:
            self._capture_seen = snap.timestamp
            if snap.speech and not self._is_robot_speaking():
                self._capture_values.append(float(snap.board_degrees))
        if len(self._capture_values) >= CAPTURE_MIN_READINGS * 2:
            self._finish_capture()
        elif time.monotonic() - self._capture_started >= CAPTURE_SECONDS:
            self._finish_capture()
        elif self._capture_kind is not None:
            self.capture_progress.emit(f"ได้ยิน {len(self._capture_values)} ค่า (ต้องการอย่างน้อย {CAPTURE_MIN_READINGS})")

    def _finish_capture(self) -> None:
        kind, values = self._capture_kind, self._capture_values
        self._capture_kind, self._capture_values = None, []
        self._timer.setInterval(TICK_MS)
        result = self._evaluate_capture(kind, values)
        self.capture_finished.emit(result)

    def _evaluate_capture(self, kind: str | None, values: list[float]) -> CaptureResult:
        if len(values) < CAPTURE_MIN_READINGS:
            return CaptureResult(
                False,
                f"ได้ยินเสียงพูดแค่ {len(values)} ค่า (ต้องการ {CAPTURE_MIN_READINGS}) "
                "พูดให้ดังและต่อเนื่องขึ้น ใกล้ไมค์ขึ้น แล้ววัดใหม่",
                readings=len(values),
            )
        mean, spread = circular_mean_degrees(values)
        if spread > CAPTURE_MAX_SPREAD_DEG:
            return CaptureResult(
                False,
                f"ทิศแกว่งเกินไป (±{spread:.0f}°) อาจมีเสียงอื่นปน หรือยืนไม่นิ่ง วัดใหม่ในที่เงียบกว่านี้",
                mean, spread, len(values),
            )
        s = self._settings
        if kind == "front":
            self._write(replace(s, front_deg=mean, front_known=True, mode=s.mode))
            return CaptureResult(True, f"ตั้งทิศหน้าของไมค์ = {mean:.0f}° (บอร์ด) จาก {len(values)} ค่า "
                                 f"คลาด ±{spread:.0f}°", mean, spread, len(values))
        turn = wrap_degrees(mean - s.front_deg)
        if abs(turn) < SIGN_MIN_TURN_DEG:
            return CaptureResult(
                False,
                f"ตำแหน่งนี้ต่างจากด้านหน้าแค่ {abs(turn):.0f}° ให้ยืนเยื้องไปทางขวาของกล้องให้มากกว่านี้ (~45°)",
                mean, spread, len(values),
            )
        sign = 1 if turn > 0 else -1
        self._write(replace(s, sign=sign, sign_known=True))
        return CaptureResult(
            True,
            f"ทิศทางหมุน = {sign:+d} (บอร์ดอ่านได้ {mean:.0f}° ต่างจากหน้า {turn:+.0f}°)",
            mean, spread, len(values),
        )


def circular_mean_degrees(values: list[float]) -> tuple[float, float]:
    """(mean angle 0-359, circular standard deviation in degrees) of board angles."""
    s = sum(math.sin(math.radians(v)) for v in values) / len(values)
    c = sum(math.cos(math.radians(v)) for v in values) / len(values)
    resultant = math.hypot(s, c)
    spread = math.degrees(math.sqrt(max(0.0, -2.0 * math.log(max(resultant, 1e-9)))))
    return math.degrees(math.atan2(s, c)) % 360.0, spread
