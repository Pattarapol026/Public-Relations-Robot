"""Telling "หันซ้าย 70 องศา", "เดินหน้า 20 เซน" and "กลับมาที่เดิม" from other words.

Plain rules on the visitor's own words, no AI model. Only a short sentence made of the command, an amount and polite
filler counts: anything with more in it ("ห้องน้ำอยู่ทางซ้ายไหม") is a question. A turn without an angle is DEFAULT_TURN_DEG
degrees and a move without a distance DEFAULT_MOVE_CM centimetres.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

DEFAULT_TURN_DEG = 50
DEFAULT_MOVE_CM = 30

KIND_DRIVE = "drive"
KIND_RETURN = "return"  # back to where the robot started


@dataclass(frozen=True)
class DriveIntent:
    kind: str
    direction: str = ""  # left | right | forward | backward
    amount: int | None = None  # degrees or centimetres; None = not said
    amount_said: bool = False

    @property
    def effective_amount(self) -> int:
        if self.amount is not None:
            return self.amount
        return DEFAULT_TURN_DEG if self.direction in ("left", "right") else DEFAULT_MOVE_CM


_DIRECTIONS = (
    ("left", re.compile(r"(?:หัน|หั่น|ถัน|เลี้ยว|หมุน|เบี่ยง)\s*(?:ตัว)?\s*(?:ไป)?\s*(?:ทาง)?\s*ซ้าย|turn\s*left|rotate\s*left|spin\s*left")),
    ("right", re.compile(r"(?:หัน|หั่น|ถัน|เลี้ยว|หมุน|เบี่ยง)\s*(?:ตัว)?\s*(?:ไป)?\s*(?:ทาง)?\s*ขวา|turn\s*right|rotate\s*right|spin\s*right")),
    ("forward", re.compile(r"(?:เดิน|เคลื่อนที่|ขยับ)?\s*(?:ไป)?\s*ข้างหน้า|เดิน\s*หน้า|(?:go|move|walk)\s*forwards?|forwards?")),
    ("backward", re.compile(r"(?:เดิน)?\s*ถอย\s*หลัง|เดิน\s*ถอย|ถอย|(?:เดิน)?\s*ไป\s*ข้างหลัง|(?:go|move|walk)\s*back(?:wards?)?|back\s*up|backwards?")),
)
_NUMBER = r"(\d+(?:\.\d+)?|[ก-๙]+?)"
_AMOUNT = re.compile(
    rf"{_NUMBER}\s*(องศา|degrees?|deg|°|เซนติเมตร|เซน|ซม\.?|เซ็นติเมตร|เซ็น|centimet(?:er|re)s?|cm|เมตร|meters?|metres?|m)(?![a-z])"
)
_HALF_METRE = re.compile(r"ครึ่ง\s*เมตร|half\s*a?\s*metre|half\s*a?\s*meter")
_FILLER = re.compile(
    r"ช่วย|หน่อย|ครับ|ค่ะ|คะ|นะ|เลย|ให้|ผม|ฉัน|หนู|หุ่นยนต์|ไอร่า|aira|robot|please|ที|สิ|ซิ|จ้ะ|จ้า|ด้วย|ไป|ตัว|ทาง|ๆ|เอา|ขอ|และ|แล้ว|แค่|หน่อยนะ|the|to|a|by|for|now"
)
_QUESTION = re.compile(r"ไหน|ยังไง|อย่างไร|ไหม|มั้ย|เปล่า|กี่|ทำไม|อะไร|เท่าไ|ได้หรือ|how|where|what|why|when|which|can you|could you|\?")
_RETURN = re.compile(
    r"(?:กลับ|หัน|ไป|เดิน|ถอย)*\s*(?:กลับ)\s*(?:มา|ไป)?\s*(?:ที่|ยัง|สู่)?\s*(?:ตำแหน่ง|จุด)?\s*(?:เดิม|เริ่มต้น|เริ่ม|แรก)"
    r"|(?:ไป|มา)\s*ที่\s*(?:เดิม|ตำแหน่งเดิม)|ตำแหน่งเดิม|จุดเริ่มต้น"
    r"|(?:go|return|come|move|get)\s*back\s*(?:to\s*)?(?:the\s*)?(?:start|original|first|home|where\s*you\s*(?:were|started))"
    r"|back\s*to\s*(?:the\s*)?(?:start|original|first)|return\s*to\s*(?:the\s*)?(?:start|original|first)"
)


def _number(text: str) -> float | None:
    try:
        return float(text)
    except ValueError:
        pass
    try:
        from pythainlp.util import thaiword_to_num

        return float(thaiword_to_num(text))
    except Exception:
        return None


def _is_only_filler(rest: str) -> bool:
    rest = _FILLER.sub("", re.sub(r"[\s.,!?\"'’“”]+", "", rest.lower()))
    return not rest.strip()


def parse(text: str) -> DriveIntent | None:
    """What ``text`` asks the robot to do, or None if it is not a drive command."""
    lowered = text.lower().strip()
    if not lowered or len(lowered) > 60 or _QUESTION.search(lowered):
        return None
    squeezed = re.sub(r"[\s.,!?]+", " ", lowered).strip()

    found = [(name, pattern.search(squeezed)) for name, pattern in _DIRECTIONS]
    found = [(name, match) for name, match in found if match]
    if not found:
        match = _RETURN.search(squeezed)
        if match and _is_only_filler(squeezed[: match.start()] + squeezed[match.end():]):
            return DriveIntent(KIND_RETURN)
        return None
    # "ถอยหลัง" holds "หลัง"; "เดินหน้า" holds "หน้า": take the one that covers most of the text, and only one.
    found.sort(key=lambda item: -(item[1].end() - item[1].start()))
    if len(found) > 1 and found[0][1].start() <= found[1][1].start() < found[0][1].end():
        found = found[:1]  # a shorter one inside the longer
    if len(found) != 1:
        return None
    direction, matched = found[0]
    rest = squeezed[: matched.start()] + " " + squeezed[matched.end():]

    amount, said = None, False
    turning = direction in ("left", "right")
    half = _HALF_METRE.search(rest)
    if half:
        if turning:
            return None
        amount, said, rest = 50, True, rest[: half.start()] + rest[half.end():]
    else:
        amount_match = _AMOUNT.search(rest)
        if amount_match:
            value = _number(amount_match.group(1))
            unit = amount_match.group(2)
            if value is None or value <= 0:
                return None
            is_angle = unit in ("องศา", "deg", "°") or unit.startswith("degree")
            if is_angle != turning:
                return None  # "turn left 20 cm" or "forward 70 degrees"
            if unit in ("เมตร", "m") or unit.startswith(("meter", "metre")):
                value *= 100
            amount, said = int(round(value)), True
            rest = rest[: amount_match.start()] + rest[amount_match.end():]
        else:
            bare = re.search(r"\d+", rest)
            if bare:
                amount, said = int(bare.group()), True
                rest = rest[: bare.start()] + rest[bare.end():]
    if not _is_only_filler(rest):
        return None
    return DriveIntent(KIND_DRIVE, direction, amount, said)
