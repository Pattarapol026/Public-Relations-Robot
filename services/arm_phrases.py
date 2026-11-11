"""Words that ask the robot to make an arm gesture, and what it says as it does.

Only the short phrase for the gesture counts ("67", "ทำท่า 67", "six seven"): a sentence that merely holds the number
("ขอนแก่นส่ง 67 ชิ้น") is a question and is left to be answered.
"""
from __future__ import annotations

import re

SIX_SEVEN = "six_seven"

_IGNORED = re.compile(r"[\s.,!?\"'’“”\-_]+")
_PREFIXES = ("ขอดูท่า", "ทำท่า", "ลองทำท่า", "เล่นท่า", "ทำให้ดู", "ขอท่า", "ท่า", "ทำ", "พูดว่า", "ช่วย", "กรุณา", "ลอง", "ขอให้", "say", "do", "please")
_TAILS = ("ให้ดูหน่อย", "ให้ดู", "ได้ไหม", "ได้มั้ย", "นะครับ", "นะคะ", "ครับ", "ค่ะ", "คะ", "หน่อย", "ด้วย", "นะ", "สิ", "ซิ", "please")
_SIX_SEVEN = frozenset({
    "67", "6และ7", "หกเจ็ด", "หกสิบเจ็ด", "หกเซเว่น", "sixseven", "six7", "6seven", "sixsevenn", "sixtyseven",
    "ซิกซ์เซเว่น", "ซิกเซเว่น", "ซิกส์เซเว่น", "ซิกซ์เซเวน", "ซิกเซเวน", "ซิกซ์เซเว่นท์",
})
_REPLY = {SIX_SEVEN: ("Six seven.", "Six seven.")}


def _core(text: str) -> str:
    text = _IGNORED.sub("", text.lower())
    changed = True
    while changed:
        changed = False
        for tail in _TAILS:
            if text.endswith(tail) and len(text) > len(tail):
                text, changed = text[: -len(tail)], True
        for prefix in _PREFIXES:
            if text.startswith(prefix) and len(text) > len(prefix):
                text, changed = text[len(prefix):], True
    return text


def core(text: str) -> str:
    """``text`` without spaces, punctuation, a leading "ทำท่า" and polite endings, to compare with a pose's phrases."""
    return _core(text)


def gesture_for(text: str) -> str | None:
    """The arm gesture id that ``text`` asks for, or None."""
    return SIX_SEVEN if _core(text) in _SIX_SEVEN else None


def reply_for(gesture: str, english: bool = True) -> str:
    """What the robot says while it makes the gesture (once)."""
    return _REPLY[gesture][0]
