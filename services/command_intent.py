"""Recognises a robot command in what a visitor said or typed.

Plain rules on the visitor's own words: no AI model takes part, and the only
possible outcomes are stop and the actions listed in the configuration. Anything
that does not clearly look like a command is left to be answered as a question.
The robot does not walk, so "take me to the lobby" is a question too.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Sequence

from config.settings import NamedTarget

INTENT_STOP = "stop"
INTENT_ACTION = "action"

_IGNORED = re.compile(r"[\s.,!?\"'’“”​]+")
# Polite or softening endings that carry no meaning of their own, longest first.
_TAILS = (
    "ได้หรือเปล่า", "ได้รึเปล่า", "ได้ไหม", "ได้มั้ย", "ได้ป่ะ", "ได้ปะ",
    "นะครับ", "นะคะ", "ครับ", "ค่ะ", "คะ", "จ้ะ", "จ้า", "หน่อย", "ด้วย", "นะ", "สิ", "ซิ", "ที",
    "please",
)
_STOP_WORDS = frozenset({
    "หยุด", "หยุดก่อน", "หยุดเดิน", "หยุดเดี๋ยวนี้", "หยุดหุ่นยนต์", "หยุดได้แล้ว", "หยุดเลย",
    "stop", "stopnow", "stoptherobot", "stopit",
})
# Words that make a sentence a question about something, not a request to do it.
_QUESTION_MARKERS = (
    "ไหน", "ยังไง", "อย่างไร", "ไหม", "มั้ย", "เปล่า", "กี่", "ทำไม", "อะไร", "เท่าไ",
    "how", "where", "what", "why", "when", "which",
)
# A command to perform an action is short: the action's name plus a few words around it.
_ACTION_EXTRA_CHARS = 12


@dataclass(frozen=True)
class Intent:
    kind: str
    target: NamedTarget | None = None


def normalize(text: str) -> str:
    """Lower case, no spaces or punctuation: speech-to-text spaces Thai unpredictably."""
    return _IGNORED.sub("", text.lower())


def _without_tails(text: str) -> str:
    changed = True
    while changed:
        changed = False
        for tail in _TAILS:
            if text.endswith(tail) and len(text) > len(tail):
                text = text[: -len(tail)]
                changed = True
    return text


def parse(text: str, actions: Sequence[NamedTarget]) -> Intent | None:
    plain = normalize(text)
    core = _without_tails(plain)
    if plain in _STOP_WORDS or core in _STOP_WORDS:
        return Intent(INTENT_STOP)
    if not core or any(marker in core for marker in _QUESTION_MARKERS):
        return None

    target = _longest_match(core, actions)
    if target is not None:
        longest = max(len(phrase) for phrase in _phrases(target) if phrase in core)
        if len(core) <= longest + _ACTION_EXTRA_CHARS:
            return Intent(INTENT_ACTION, target)
    return None


def _phrases(target: NamedTarget) -> list[str]:
    return [phrase for phrase in map(normalize, (target.label, *target.phrases)) if phrase]


def _longest_match(text: str, targets: Sequence[NamedTarget]) -> NamedTarget | None:
    """The target whose longest phrase appears in ``text``."""
    best: tuple[int, NamedTarget] | None = None
    for target in targets:
        for phrase in _phrases(target):
            if phrase in text and (best is None or len(phrase) > best[0]):
                best = (len(phrase), target)
    return None if best is None else best[1]
