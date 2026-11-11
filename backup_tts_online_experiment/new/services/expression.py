"""When should the robot laugh a little, say "hmm", or "oh!"? Rarely, and only where a person would.

``ExpressionPolicy.decorate`` takes the visitor's question and the robot's answer and returns what to SPEAK:
the same answer, with at most one small sound written into it (``[chuckle]`` ``[laugh]`` ``[hmm]`` ``[aha]``
``[wow]``, played by ``audio/cues.py``). What is shown on the screen and remembered as the answer never
changes.

The rules are fixed and can be tested, not left to the AI:

* A sound goes with what the VISITOR did: laughed or joked (a chuckle), paid the robot a compliment (a shy
  chuckle), asked for a joke (a laugh after the answer), shared good news (a "wow"), corrected themselves or
  explained what they meant ("oh!"), asked something that calls for thought ("hmm").
* Never when the answer is an apology, "I don't know", a fallback, or came from a guess; never on a
  serious subject (an emergency, an injury, a complaint, grief, a safety stop).
* Never two answers in a row, never more than 2 in the last 6, and not within 25 s of the last one:
  a robot that laughs at everything is worse than one that never does.
"""
from __future__ import annotations

import random
import re
import time
from collections import deque
from typing import Callable

from services.conversation_service import SOURCE_GEMINI, SOURCE_KNOWLEDGE

COOLDOWN_S = 25.0
WINDOW = 6
MAX_IN_WINDOW = 2
THINK_CHANCE = 0.5  # of the questions that call for thought, and not asked for a joke or praise

_SERIOUS = re.compile(
    r"ฉุกเฉิน|อุบัติเหตุ|เจ็บ|ปวด|ป่วย|บาดเจ็บ|เสียชีวิต|ตาย|ร้องเรียน|ทุจริต|โกง|ถูกโกง|หาย(ไป)?|เสียใจ|โกรธ|แย่มาก|ช่วยด้วย|"
    r"ด่วน|เหตุ|ไฟไหม้|หยุด(ทันที)?|emergency|accident|injur|hurt|sick|ill\b|die|dead|death|complain|fraud|cheat|"
    r"lost|missing|urgent|help me|angry|upset|sad\b|safety",
    re.IGNORECASE,
)
_REGRET = re.compile(
    r"ขออภัย|ขอโทษ|ไม่ทราบ|ไม่มีข้อมูล|ไม่สามารถ|ไม่แน่ใจ|ยังไม่|ไม่พบ|sorry|apolog|unfortunately|i don't know|"
    r"i do not know|not sure|can't|cannot|no information",
    re.IGNORECASE,
)
_JOKE_REQUEST = re.compile(r"เล่ามุก|มุกตลก|เรื่องตลก|ขอมุก|เล่าเรื่องขำ|tell me a joke|a joke|make me laugh|something funny", re.IGNORECASE)
_LAUGHTER = re.compile(r"5{3,}|ฮ่า|ฮา+\b|ขำ|ตลกดี|ตลกจัง|55+|ha ?ha|he ?he|lol|lmao|funny|😂|🤣|😆", re.IGNORECASE)
_COMPLIMENT = re.compile(
    r"เก่งมาก|เก่งจัง|น่ารัก|ฉลาด|สุดยอด|เจ๋ง|เท่จัง|ชอบ(หุ่น|ไอร่า|aira|เธอ|คุณ)|หุ่นยนต์(ตัวนี้)?(เก่ง|เจ๋ง|น่ารัก|ฉลาด)|"
    r"you(?:'re| are) (?:so |very |really )?(?:smart|cute|cool|amazing|awesome|great|clever|funny)|good robot|"
    r"i (?:like|love) you|love (?:this|the) robot",
    re.IGNORECASE,
)
_GOOD_NEWS = re.compile(
    r"ได้รางวัล|ได้ที่ ?1|ได้ที่หนึ่ง|ชนะ|สอบติด|ติดแล้ว|ผ่านแล้ว|ผ่านการคัดเลือก|เหรียญทอง|we won|first (?:prize|place)|got (?:in|accepted)|"
    r"passed|gold medal",
    re.IGNORECASE,
)
_CLARIFIES = re.compile(
    r"หมายถึง|หมายความว่า|ที่จริง|จริงๆ แล้ว|ไม่ใช่|ผมพูดผิด|พูดผิด|i mean|i meant|what i meant|actually|no,? i|sorry,? i meant",
    re.IGNORECASE,
)
_CALLS_FOR_THOUGHT = re.compile(
    r"ทำไม|อย่างไรดี|ยังไงดี|ดีกว่ากัน|ควร.{0,20}(ดี|ไหม)|แนะนำ|คิดว่า|เลือก.{0,15}ดี|เหมาะกับ|what do you think|which (?:one )?(?:is better|should)|"
    r"why |should i|recommend|better",
    re.IGNORECASE,
)


class ExpressionPolicy:
    def __init__(self, clock: Callable[[], float] = time.monotonic, rng: random.Random | None = None) -> None:
        self._clock = clock
        self._rng = rng or random.Random()
        self._recent: deque[bool] = deque(maxlen=WINDOW)  # for each answer: did it carry a sound?
        self._last_at = float("-inf")

    def decorate(self, question: str, answer: str, source: str, language: str = "th") -> str:
        """``answer`` for speaking, with a sound written into it where one fits (or unchanged)."""
        choice = self.choose(question, answer, source)
        self._recent.append(choice is not None)
        if choice is None:
            return answer
        self._last_at = self._clock()
        name, where = choice
        return f"{answer} [{name}]" if where == "end" else f"[{name}] {answer}"

    def choose(self, question: str, answer: str, source: str) -> tuple[str, str] | None:
        """``(sound, "start" | "end")`` or None. Does not record anything."""
        if source not in (SOURCE_KNOWLEDGE, SOURCE_GEMINI):
            return None  # no information, a fallback, a best guess: nothing to be light about
        if _SERIOUS.search(question) or _SERIOUS.search(answer) or _REGRET.search(answer):
            return None
        if self._recent and self._recent[-1]:
            return None  # never twice in a row
        if sum(self._recent) >= MAX_IN_WINDOW or self._clock() - self._last_at < COOLDOWN_S:
            return None
        if _JOKE_REQUEST.search(question):
            return "laugh", "end"
        if _GOOD_NEWS.search(question):
            return "wow", "start"
        if _LAUGHTER.search(question) or _COMPLIMENT.search(question):
            return "chuckle", "start"
        if _CLARIFIES.search(question):
            return "aha", "start"
        if source == SOURCE_GEMINI and len(question) >= 14 and _CALLS_FOR_THOUGHT.search(question):
            return ("hmm", "start") if self._rng.random() < THINK_CHANCE else None
        return None
