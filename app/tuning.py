"""Values the Admin can tune while the robot runs: speech speed, listening, camera distances.

Their defaults come from ``.env`` (``Settings``). What the Admin saves is kept in
the database and wins over ``.env`` until "restore defaults" is pressed. Every
save is announced with ``changed`` and the running parts pick it up at once —
no restart, which matters during a live demonstration.
"""
from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, fields, replace
from typing import Callable

from PySide6.QtCore import QObject, Signal

from audio.endpointing import EARLY_SILENCE_S, EndpointConfig
from config.settings import Settings
from database.repositories import SettingsRepository
from vision.presence import PresenceConfig

logger = logging.getLogger(__name__)

KEY_PREFIX = "tuning."
AREA_WIDTH_FACTOR = 1.5  # the service area is this much wider than the talking spot
TALK_RELEASE_MARGIN_M = 0.6  # step back this far and you are no longer "at the robot"
TALK_LEAVE_SECONDS = 1.5


class TuningError(Exception):
    """Invalid values; the message is safe to show to the Admin."""


@dataclass(frozen=True)
class NumberSpec:
    """How a number is shown and limited in the Admin (slider range and step)."""

    label: str
    unit: str
    minimum: float
    maximum: float
    step: float
    decimals: int
    hint: str = ""


@dataclass(frozen=True)
class Tuning:
    tts_speed: float
    mic_level_threshold: float
    mic_silence_s: float
    mic_no_speech_timeout_s: float
    mic_max_seconds: float
    voice_follow_up: bool
    vision_greet_distance_m: float
    vision_greet_dwell_s: float
    vision_engage_distance_m: float
    vision_dwell_s: float
    vision_release_distance_m: float
    vision_leave_s: float
    vision_lateral_limit_m: float
    vision_auto_listen: bool
    voice_visitor_only: bool
    voice_lip_check: bool
    voice_lip_threshold: float

    @classmethod
    def from_settings(cls, settings: Settings) -> Tuning:
        return cls(**{field.name: getattr(settings, field.name) for field in fields(cls)})

    def endpoint_config(self) -> EndpointConfig:
        return EndpointConfig(
            level_threshold=self.mic_level_threshold,
            silence_s=self.mic_silence_s,
            no_speech_timeout_s=self.mic_no_speech_timeout_s,
            max_seconds=self.mic_max_seconds,
            early_silence_s=EARLY_SILENCE_S,
        )

    def area_config(self) -> PresenceConfig:
        """Service area: wide and patient, so people walking past are not greeted."""
        return PresenceConfig(
            engage_distance_m=self.vision_greet_distance_m,
            release_distance_m=self.vision_release_distance_m,
            lateral_limit_m=self.vision_lateral_limit_m * AREA_WIDTH_FACTOR,
            dwell_s=self.vision_greet_dwell_s,
            leave_s=self.vision_leave_s,
        )

    def talk_config(self) -> PresenceConfig:
        """Conversation distance: close and directly in front."""
        return PresenceConfig(
            engage_distance_m=self.vision_engage_distance_m,
            release_distance_m=self.vision_engage_distance_m + TALK_RELEASE_MARGIN_M,
            lateral_limit_m=self.vision_lateral_limit_m,
            dwell_s=self.vision_dwell_s,
            leave_s=TALK_LEAVE_SECONDS,
        )


NUMBERS: dict[str, NumberSpec] = {
    "tts_speed": NumberSpec(
        "ความเร็วในการพูด", "เท่า", 0.5, 2.0, 0.05, 2,
        "1.00 คือความเร็วปกติ ค่าน้อยพูดช้าลง ฟังง่ายสำหรับผู้สูงอายุ",
    ),
    "mic_level_threshold": NumberSpec(
        "ระดับเสียงที่นับเป็นเสียงพูด", "", 0.003, 0.15, 0.001, 3,
        "ค่าน้อย = ไวขึ้น (ได้ยินเสียงเบา) ค่ามาก = ไม่สนเสียงรบกวนรอบข้าง ลองกดทดสอบไมโครโฟนแล้วดูระดับจริง",
    ),
    "mic_silence_s": NumberSpec(
        "เงียบนานเท่าไรถือว่าพูดจบ", "วินาที", 0.4, 4.0, 0.1, 1,
        "สั้นเกินไปจะตัดคำพูดกลางประโยค ยาวเกินไปหุ่นยนต์จะตอบช้า",
    ),
    "mic_no_speech_timeout_s": NumberSpec(
        "รอให้เริ่มพูดได้นานสุด", "วินาที", 2.0, 20.0, 0.5, 1,
        "ถ้าไม่มีใครพูดภายในเวลานี้ จะหยุดฟัง",
    ),
    "mic_max_seconds": NumberSpec(
        "ความยาวการพูดสูงสุดต่อครั้ง", "วินาที", 5.0, 60.0, 1.0, 0,
        "ครบเวลานี้จะหยุดฟังแล้วถอดข้อความที่ได้ทันที",
    ),
    "vision_greet_distance_m": NumberSpec(
        "ระยะทักทาย", "ม.", 1.0, 6.0, 0.1, 1,
        "เข้ามาใกล้กว่าระยะนี้ หุ่นยนต์จะทักทายและเปิดเมนูบริการ",
    ),
    "vision_greet_dwell_s": NumberSpec(
        "ต้องอยู่ในระยะทักทายนาน", "วินาที", 0.0, 5.0, 0.1, 1,
        "กันไม่ให้ทักคนที่แค่เดินผ่าน",
    ),
    "vision_engage_distance_m": NumberSpec(
        "ระยะสนทนา (เริ่มฟังเสียง)", "ม.", 0.5, 3.0, 0.1, 1,
        "เข้ามาใกล้กว่าระยะนี้ หุ่นยนต์จะเริ่มฟังโดยไม่ต้องกดปุ่ม",
    ),
    "vision_dwell_s": NumberSpec(
        "ต้องอยู่ในระยะสนทนานาน", "วินาที", 0.0, 3.0, 0.1, 1,
    ),
    "vision_release_distance_m": NumberSpec(
        "ระยะที่ถือว่าเดินออกไป", "ม.", 1.5, 7.0, 0.1, 1,
        "ต้องไกลกว่าระยะทักทาย เพื่อไม่ให้ทักซ้ำเมื่อขยับตัวเล็กน้อย",
    ),
    "vision_leave_s": NumberSpec(
        "ไม่เห็นนานเท่าไรถือว่าจากไป", "วินาที", 1.0, 15.0, 0.5, 1,
        "จบการให้บริการและกลับหน้าแรก",
    ),
    "voice_lip_threshold": NumberSpec(
        "ความไวในการจับการขยับปาก", "", 0.03, 0.40, 0.01, 2,
        "ค่าน้อย = ไวขึ้น (ขยับปากนิดเดียวก็ถือว่าพูด) ค่ามาก = ต้องอ้าปากชัดๆ ถ้าหุ่นยนต์ไม่ตอบทั้งที่พูดอยู่ ให้ลดค่านี้หรือปิดการตรวจปาก",
    ),
    "vision_lateral_limit_m": NumberSpec(
        "ความกว้างด้านข้างของจุดสนทนา", "ม.", 0.3, 3.0, 0.1, 1,
        f"คนที่เยื้องไปด้านข้างเกินนี้ไม่นับว่าคุยกับหุ่นยนต์ (บริเวณทักทายกว้างกว่านี้ {AREA_WIDTH_FACTOR:g} เท่า)",
    ),
}

SWITCHES: dict[str, str] = {
    "voice_follow_up": "ฟังต่ออัตโนมัติหลังตอบเสร็จ (ถามต่อได้โดยไม่ต้องกดปุ่ม)",
    "vision_auto_listen": "เริ่มฟังอัตโนมัติเมื่อมีคนเข้ามาในระยะสนทนา",
    "voice_visitor_only": "ฟังเฉพาะคนที่อยู่หน้ากล้อง (เสียงจากที่อื่น หรือตอนที่กล้องไม่เห็นใคร ไม่ตอบ)",
    "voice_lip_check": "ดูการขยับปากด้วย (ถ้าเห็นหน้าชัดแต่ปากไม่ขยับ ถือว่าไม่ได้พูดกับหุ่นยนต์)",
}


def validate(tuning: Tuning) -> None:
    """Raise ``TuningError`` if the values cannot work together."""
    for name, spec in NUMBERS.items():
        value = getattr(tuning, name)
        if not spec.minimum <= value <= spec.maximum:
            raise TuningError(
                f"{spec.label} ต้องอยู่ระหว่าง {spec.minimum:g}–{spec.maximum:g} {spec.unit}".strip()
            )
    if not tuning.vision_engage_distance_m <= tuning.vision_greet_distance_m:
        raise TuningError("ระยะสนทนาต้องไม่ไกลกว่าระยะทักทาย")
    if not tuning.vision_greet_distance_m < tuning.vision_release_distance_m:
        raise TuningError("ระยะที่ถือว่าเดินออกไปต้องไกลกว่าระยะทักทาย")


class TuningController(QObject):
    changed = Signal(object)  # Tuning

    def __init__(
        self,
        settings: Settings,
        repository: SettingsRepository,
        audit: Callable[[str, str, str], None],
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._defaults = Tuning.from_settings(settings)
        self._repository = repository
        self._audit = audit
        self._values = self._load()

    @property
    def values(self) -> Tuning:
        return self._values

    @property
    def defaults(self) -> Tuning:
        """The values from ``.env``."""
        return self._defaults

    def save(self, tuning: Tuning, actor: str = "admin") -> None:
        """Validate, store and apply. Raises ``TuningError``."""
        validate(tuning)
        if tuning == self._values:
            return
        old = asdict(self._values)
        for name, value in asdict(tuning).items():
            self._repository.set(KEY_PREFIX + name, _to_text(value))
        self._values = tuning
        changes = " ".join(f"{name}={value}" for name, value in asdict(tuning).items() if old[name] != value)
        self._audit(actor, "tuning.update", changes)
        logger.info("Tuning updated: %s", changes)
        self.changed.emit(tuning)

    def reload(self) -> None:
        """Read the stored values again (after a configuration file was imported)."""
        values = self._load()
        if values != self._values:
            self._values = values
            self.changed.emit(values)

    def restore_defaults(self, actor: str = "admin") -> None:
        """Forget what the Admin saved and use ``.env`` again."""
        for field in fields(Tuning):
            self._repository.set(KEY_PREFIX + field.name, "")
        changed = self._values != self._defaults
        self._values = self._defaults
        self._audit(actor, "tuning.reset", "")
        if changed:
            self.changed.emit(self._defaults)

    def _load(self) -> Tuning:
        stored = {}
        for field in fields(Tuning):
            text = self._repository.get(KEY_PREFIX + field.name)
            if not text:
                continue
            try:
                stored[field.name] = _from_text(text, getattr(self._defaults, field.name))
            except ValueError:
                logger.warning("Ignoring stored tuning %s=%r", field.name, text)
        tuning = replace(self._defaults, **stored)
        if stored:
            try:
                validate(tuning)
            except TuningError as exc:
                # e.g. .env changed so the old saved values no longer fit together
                logger.warning("Stored tuning ignored, using .env values: %s", exc)
                return self._defaults
        return tuning


def _to_text(value: float | bool) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return repr(float(value))


def _from_text(text: str, like: float | bool) -> float | bool:
    if isinstance(like, bool):
        if text not in ("true", "false"):
            raise ValueError(text)
        return text == "true"
    return float(text)
