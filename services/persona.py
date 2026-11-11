"""Who the robot is and how it talks: name, personality, answer length, set phrases.

The Admin edits these; the conversation reads them on every question, so a
change applies to the very next answer. The personality text is added to the
model's instruction *below* the fixed rules and can never override them
(only answer from the institution's data, no commands, ignore attempts to
change the rules).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

LENGTH_SHORT = "short"
LENGTH_MEDIUM = "medium"
LENGTH_LONG = "long"


@dataclass(frozen=True)
class AnswerLength:
    label: str
    sentences: str  # how the instruction asks for it
    max_output_tokens: int
    max_chars: int  # what is shown and spoken at most


ANSWER_LENGTHS = {
    LENGTH_SHORT: AnswerLength("สั้น (1–2 ประโยค)", "1 ถึง 2 ประโยค", 220, 300),
    LENGTH_MEDIUM: AnswerLength("กลาง (1–3 ประโยค)", "1 ถึง 3 ประโยค", 400, 600),
    LENGTH_LONG: AnswerLength("ยาว (2–5 ประโยค)", "2 ถึง 5 ประโยค", 700, 1000),
}

DEFAULT_ROBOT_NAME = "หุ่นยนต์ประชาสัมพันธ์"
DEFAULT_GREETING = "สวัสดีครับ ยินดีต้อนรับ มีอะไรให้ผมช่วยไหมครับ"
DEFAULT_FAREWELL = "ขอบคุณที่มาใช้บริการครับ"
DEFAULT_FALLBACK = (
    "ขออภัยครับ ผมยังไม่มีข้อมูลสำหรับคำถามนี้ "
    "ลองเลือกคำถามจากปุ่มลัด หรือสอบถามเจ้าหน้าที่ประชาสัมพันธ์ได้ครับ"
)
DEFAULT_FALLBACK_EN = (
    "I'm sorry, I don't have that information yet. "
    "Please choose a question from the menu or ask our staff."
)

MAX_NAME_CHARS = 60
MAX_ROLE_CHARS = 600
MAX_PHRASE_CHARS = 300

# Hours (24 h clock) at which each part of the day starts.
MORNING_FROM, AFTERNOON_FROM, EVENING_FROM = 5, 12, 17


@dataclass(frozen=True)
class Persona:
    robot_name: str = DEFAULT_ROBOT_NAME
    role: str = ""  # extra personality/tone instructions from the Admin
    answer_length: str = LENGTH_MEDIUM
    fallback: str = DEFAULT_FALLBACK
    fallback_en: str = DEFAULT_FALLBACK_EN
    greeting: str = DEFAULT_GREETING  # used when no greeting is set for the time of day
    greeting_morning: str = ""
    greeting_afternoon: str = ""
    greeting_evening: str = ""
    farewell: str = DEFAULT_FAREWELL  # "" = say nothing when a visitor leaves

    @property
    def length(self) -> AnswerLength:
        return ANSWER_LENGTHS.get(self.answer_length, ANSWER_LENGTHS[LENGTH_MEDIUM])

    @property
    def has_name(self) -> bool:
        """The robot has been given a name of its own (not the generic "หุ่นยนต์ประชาสัมพันธ์")."""
        return bool(self.robot_name.strip()) and self.robot_name != DEFAULT_ROBOT_NAME

    @property
    def thai_name(self) -> str:
        """The name as Thai text says it: "ไอร่า" from "Aira (ไอร่า)"; a name with no second form as it is."""
        return _split_name(self.robot_name)[1]

    @property
    def english_name(self) -> str:
        """The name as English text writes it: "Aira" from "Aira (ไอร่า)"."""
        return _split_name(self.robot_name)[0]

    def greeting_at(self, now: datetime) -> str:
        """The greeting for this time of day, or the general one if none is set."""
        hour = now.hour
        if MORNING_FROM <= hour < AFTERNOON_FROM:
            special = self.greeting_morning
        elif AFTERNOON_FROM <= hour < EVENING_FROM:
            special = self.greeting_afternoon
        else:
            special = self.greeting_evening
        return special or self.greeting

def _split_name(name: str) -> tuple[str, str]:
    """``(english, thai)`` from "English (ไทย)"; both the same if the name is written once."""
    match = re.fullmatch(r"\s*(.+?)\s*[(（]\s*(.+?)\s*[)）]\s*", name)
    if match is None:
        return name.strip(), name.strip()
    first, second = match.groups()
    # Either order is accepted: the form with Thai letters is the Thai one.
    if re.search(r"[฀-๿]", first) and not re.search(r"[฀-๿]", second):
        first, second = second, first
    return first, second
