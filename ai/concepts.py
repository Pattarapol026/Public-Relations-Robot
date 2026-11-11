"""Words that mean the same thing to a visitor, in Thai and English.

The knowledge base is matched on its own wording (see ``knowledge_service``).
That misses a question asked with other words — "ค่าเล่าเรียน" for an entry
about "ค่าเทอม" — and every English question about a Thai entry. These groups
connect them: a question and an entry that mention the same concept are
related, whichever words they use.

Two kinds of concept:

* a **topic** is what the question is about (tuition, the library, ...).
  An entry about a different topic must not answer it, so a topic in the
  question that the entry does not mention counts against that entry;
* an **aspect** is what is asked about the topic (where, when, how to contact).

The groups are general public-relations vocabulary, not one institution's
facts; anything specific belongs in the entries' own keywords (Admin →
ฐานความรู้ → คำค้น), which work in any language.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache

TOPIC = "topic"
ASPECT = "aspect"
# Aspects that other aspects can answer ("how do I apply?" -> "at the registration office").
LOOSE_ASPECTS = frozenset({"วิธีการ"})


@dataclass(frozen=True)
class Concept:
    name: str
    kind: str
    thai: tuple[str, ...]
    english: tuple[str, ...]  # whole words or phrases; a trailing "s" is also accepted


CONCEPTS: tuple[Concept, ...] = (
    # --- topics ---
    Concept("ค่าใช้จ่าย", TOPIC,
            ("ค่าเทอม", "ค่าเล่าเรียน", "ค่าธรรมเนียม", "ค่าใช้จ่าย", "ค่าเรียน", "ค่าบำรุง", "ค่าสมัคร",
             "เท่าไหร่", "เท่าไร", "กี่บาท", "ราคา"),
            ("tuition", "fee", "cost", "price", "how much", "expense")),
    Concept("การสมัคร", TOPIC,
            ("สมัครเรียน", "รับสมัคร", "สมัคร", "ลงทะเบียน", "ทะเบียน", "รับนักศึกษา", "เข้าเรียน"),
            ("apply", "application", "admission", "enrol", "enroll", "enrolment", "enrollment",
             "register", "registration", "sign up")),
    Concept("เอกสาร", TOPIC,
            ("เอกสาร", "หลักฐาน", "สำเนา", "ใบรับรอง", "ใบระเบียน", "ทรานสคริปต์"),
            ("document", "paperwork", "certificate", "transcript", "copy of")),
    Concept("คุณสมบัติ", TOPIC,
            ("คุณสมบัติ", "เงื่อนไข", "เกณฑ์", "ผู้มีสิทธิ์"),
            ("requirement", "qualification", "eligible", "eligibility", "criteria")),
    Concept("หลักสูตร", TOPIC,
            ("แผนกวิชา", "แผนก", "สาขาวิชา", "สาขา", "หลักสูตร", "ภาควิชา", "วิชาเอก", "คณะ"),
            ("department", "major", "program", "programme", "course", "curriculum", "faculty",
             "subject", "study")),
    Concept("ทุน", TOPIC,
            ("ทุนการศึกษา", "ทุน", "กยศ", "กู้ยืม"),
            ("scholarship", "grant", "student loan", "loan")),
    Concept("กิจกรรม", TOPIC,
            ("กิจกรรม", "อีเวนต์", "นิทรรศการ", "งานประจำปี"),
            ("event", "activity", "activities", "exhibition", "open house")),
    Concept("ระเบียบ", TOPIC,
            ("ระเบียบ", "กฎ", "ข้อบังคับ", "เครื่องแบบ", "การแต่งกาย", "แต่งกาย"),
            ("rule", "regulation", "uniform", "dress code", "policy")),
    Concept("ฝึกงาน", TOPIC,
            ("ฝึกงาน", "ฝึกประสบการณ์", "สหกิจ", "ทวิภาคี"),
            ("internship", "intern", "apprenticeship", "work placement")),
    Concept("สมัครงาน", TOPIC,
            ("สมัครงาน", "รับสมัครงาน", "ตำแหน่งงาน", "รับพนักงาน"),
            ("job", "vacancy", "vacancies", "career", "hiring")),
    Concept("ชำระเงิน", TOPIC,
            ("ชำระเงิน", "จ่ายเงิน", "การเงิน", "แคชเชียร์", "จ่ายค่า"),
            ("pay", "payment", "cashier", "billing", "bill")),
    Concept("ห้องน้ำ", TOPIC, ("ห้องน้ำ", "สุขา"), ("toilet", "restroom", "bathroom", "washroom")),
    Concept("ห้องสมุด", TOPIC, ("ห้องสมุด", "หอสมุด"), ("library",)),
    # A café is not the food court: asking for coffee must not get the canteen's answer.
    Concept("อาหาร", TOPIC,
            ("โรงอาหาร", "ร้านอาหาร", "ศูนย์อาหาร", "ที่กินข้าว"),
            ("canteen", "cafeteria", "food", "restaurant", "food court")),
    Concept("ที่จอดรถ", TOPIC, ("ที่จอดรถ", "จอดรถ", "ลานจอด"), ("parking", "car park", "park my car")),
    Concept("ห้องพยาบาล", TOPIC, ("ห้องพยาบาล", "ปฐมพยาบาล"), ("first aid", "nurse", "infirmary")),
    Concept("ประชาสัมพันธ์", TOPIC,
            ("ประชาสัมพันธ์", "โต๊ะประชาสัมพันธ์", "เคาน์เตอร์"),
            ("public relations", "information desk", "reception", "front desk", "help desk")),
    # The robot itself: "คุณคือใคร" is the entry "คุณเป็นใคร" (a visitor asked it so and got
    # "no information" while the AI was unreachable).
    Concept("ตัวตนหุ่นยนต์", TOPIC,
            ("เป็นใคร", "คือใคร", "ชื่ออะไร", "ชื่อว่าอะไร", "ชื่อไร", "แนะนำตัว"),
            ("who are you", "your name", "who is this", "introduce yourself")),
    Concept("นักศึกษาใหม่", TOPIC,
            ("นักศึกษาใหม่", "นักเรียนใหม่", "ปีหนึ่ง", "ปี 1"),
            ("new student", "freshman", "freshmen", "first-year", "first year")),
    # --- aspects ---
    Concept("เวลา", ASPECT,
            ("เวลาทำการ", "เวลาเปิด", "เวลาปิด", "กี่โมง", "เวลา", "เปิดทำการ", "เปิด", "ปิด", "วันไหน"),
            ("time", "hour", "schedule", "open", "opening", "close", "closing", "when")),
    Concept("สถานที่", ASPECT,
            ("อยู่ที่ไหน", "อยู่ไหน", "ที่ไหน", "ตรงไหน", "ทางไหน", "ตึกไหน", "อาคารไหน", "ห้องไหน",
             "ชั้นไหน", "ไปยังไง", "ไปอย่างไร", "ที่ตั้ง", "ตั้งอยู่"),
            ("where", "location", "located", "address", "find", "how do i get", "way to")),
    Concept("ติดต่อ", ASPECT,
            ("ติดต่อ", "เบอร์โทร", "โทรศัพท์", "เบอร์", "โทร", "อีเมล", "ไลน์", "เฟซบุ๊ก"),
            ("contact", "phone", "telephone", "call", "email", "e-mail", "line id", "facebook")),
    Concept("รายการ", ASPECT,
            # "อะไรบ้าน": how speech-to-text often writes "อะไรบ้าง" (in the robot's history)
            ("อะไรบ้าง", "อะไรบ้าน", "มีอะไร", "กี่แผนก", "กี่สาขา"),
            ("which", "what kind", "list of", "what are the", "do you have", "do you offer")),
    Concept("วิธีการ", ASPECT,
            ("ขั้นตอน", "วิธี", "อย่างไร", "ยังไง", "ทำยังไง"),
            ("how to", "how do", "how can", "procedure", "process", "step")),
)


# Thai letters that sound alike are folded together, tone marks and the vowel
# shortener are dropped, and a letter silenced by ์ is removed, so a speech-to-
# text spelling still meets the written one ("สมัก" ~ "สมัคร", "ประชาสัมพัน" ~
# "ประชาสัมพันธ์"). Used for matching only, never shown.
_NOT_LETTERS = re.compile(r"[\W_]+", re.UNICODE)  # as knowledge_service.normalize
# For recognising whole words the vowels must stay: without them "ทุน" would be
# found inside "ที่นี่". Only tone marks are dropped.
_NOT_LETTERS_OR_VOWELS = re.compile("[^\\wัิ-ฺ]+", re.UNICODE)
_SILENT = re.compile("[ก-ฮ][ิ-ุ]?์")
_MARKS = re.compile("[็-๋ํ]")
_SOUND_ALIKE = str.maketrans({
    "ข": "ก", "ฃ": "ก", "ค": "ก", "ฅ": "ก", "ฆ": "ก",
    "ฉ": "ช", "ฌ": "ช",
    "ซ": "ส", "ศ": "ส", "ษ": "ส",
    "ฎ": "ด", "ฏ": "ต",
    "ฐ": "ท", "ฑ": "ท", "ฒ": "ท", "ถ": "ท", "ธ": "ท",
    "ณ": "น", "ญ": "ย", "ฬ": "ล", "ร": "ล",
    "ผ": "พ", "ภ": "พ", "ฝ": "ฟ", "ฮ": "ห",
})


def sound_key(text: str) -> str:
    """``text`` folded to how it sounds, without spaces or punctuation.

    Takes the text as written: silent letters are recognised by their ์ mark,
    which normalisation would already have removed.
    """
    text = _SILENT.sub("", text.lower())
    return _NOT_LETTERS.sub("", _MARKS.sub("", text)).translate(_SOUND_ALIKE)


def _word_key(text: str) -> str:
    """Like ``sound_key`` but keeping the vowels, for finding whole words."""
    text = _SILENT.sub("", text.lower())
    return _NOT_LETTERS_OR_VOWELS.sub("", _MARKS.sub("", text)).translate(_SOUND_ALIKE)


# A term this short once folded to its sound is found as written instead: folded,
# "ไลน์" (LINE) is "ไล", which is also inside "อะไร", so every "what" asked for contact details.
MIN_SOUND_TERM = 3


def _term_key(term: str) -> str:
    key = _word_key(term)
    return key if len(key) >= MIN_SOUND_TERM else ""


@lru_cache(maxsize=1)
def _compiled() -> tuple[tuple[Concept, tuple[str, ...], re.Pattern | None], ...]:
    compiled = []
    for concept in CONCEPTS:
        thai = tuple(_term_key(term) for term in concept.thai)
        english = (
            re.compile(r"\b(?:" + "|".join(re.escape(term) + "s?" for term in concept.english) + r")\b")
            if concept.english else None
        )
        compiled.append((concept, thai, english))
    return tuple(compiled)


@lru_cache(maxsize=8192)
def find(text: str) -> frozenset[str]:
    """Names of the concepts mentioned in ``text`` (as written). Remembered: entry texts come up for every question."""
    words = _word_key(text)
    lower = text.lower()
    found = set()
    for concept, thai, english in _compiled():
        if (
            any(term and term in words for term in thai)
            or any(not key and term in lower for key, term in zip(thai, concept.thai))
            or (english and english.search(lower))
        ):
            found.add(concept.name)
    return frozenset(found)


def without_terms(text: str, names: frozenset[str]) -> tuple[str, float]:
    """``text`` with the Thai terms of the concepts ``names`` cut out, and the share that was cut.

    The rest is in the same normal form as ``find`` uses, with a space where a term was.
    """
    words = _word_key(text)
    if not words:
        return "", 0.0
    rest = words
    for concept, thai, _english in _compiled():
        if concept.name in names:
            for term in sorted(thai, key=len, reverse=True):
                if term:
                    rest = rest.replace(term, " ")
    return rest, (len(words) - len(rest.replace(" ", ""))) / len(words)


def english_words(text: str, names: frozenset[str]) -> set[str]:
    """The English words of ``text`` that belong to one of the concepts ``names``."""
    lower = text.lower()
    words: set[str] = set()
    for concept, _thai, english in _compiled():
        if english and concept.name in names:
            for match in english.finditer(lower):
                words.update(match.group(0).split())
    return words


def kind_of(name: str) -> str:
    return next(concept.kind for concept in CONCEPTS if concept.name == name)
