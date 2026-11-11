"""Finds the knowledge-base entries relevant to a question, and formats them for the model.

This is the "retrieval" half of the robot's answers: the institution's own
data (table ``knowledge_items``, edited in Admin) is selected here and handed
to Gemini as the only source it may answer from. It also works alone, as the
offline fallback when Gemini cannot be reached.

Thai is written without spaces between words, so matching uses character
trigrams instead of word splitting; it needs no dictionary. Three things make
it tolerant of how visitors really ask (measured on questions from the robot's
own history, see tests/test_answers.py):

* trigrams are taken from the *sound* of the text (``concepts.sound_key``), so
  speech-to-text spellings such as "สมัก" or "ประชาสัมพัน" still match;
* the overlap is measured both ways, so an entry's whole question found inside
  a long, rambling sentence still counts;
* shared concepts (``ai/concepts.py``) link other wordings and English questions
  to Thai entries, and a topic the entry does not cover counts against it.
"""
from __future__ import annotations

import re
from functools import lru_cache
from dataclasses import dataclass
from typing import Sequence

from ai import concepts
from database.models import KnowledgeItem

KEYWORD_WEIGHT = 3.0
QUESTION_WEIGHT = 4.0
ANSWER_WEIGHT = 1.0
# How much finding an entry's whole question inside the visitor's (longer)
# sentence counts, compared with the visitor's words being found in the entry.
COVERAGE_FACTOR = 0.85
TOPIC_WEIGHT = 1.5  # a topic both mention
ASPECT_WEIGHT = 0.75  # an aspect both mention (where, when, contact...)
MISSING_TOPIC_PENALTY = 2.5  # the question is about a topic this entry does not mention
IN_ANSWER_ONLY = 0.35  # a shared concept found only in the entry's answer counts this much
# Below this score an entry is not considered related to the question at all.
MIN_RELATED_SCORE = 0.6
# The offline fallback answers by itself only when it is this sure.
MIN_CONFIDENT_SCORE = 2.0
MIN_SHARE_OF_TOP = 0.45  # a lower-ranked entry may answer if it scores at least this share of the top

_NOT_LETTERS = re.compile(r"[\W_]+", re.UNICODE)
# Polite particles and fillers carry no meaning for matching ("ครับ", "เอ่อ").
_FILLERS = re.compile(
    r"ครับผม|ครับ|คะ|ค่ะ|ค่า|ค๊ะ|จ้ะ|จ้า|นะคะ|นะ|น่ะ|เหรอ|หรอ|ล่ะ|หน่อย|เอ่อ|อ่ะ|อ่า|อืม|ขอโทษ"
)
# Words that say how something is asked, not what it is about. What is left of a
# question without them (and without its fillers) is its subject: "นวด" in
# "มีบริการนวดไหม", "เช็กอิน" in "เช็กอินกี่โมง". An entry answers by itself only if it
# covers that subject; sharing "บริการ" or "กี่โมง" is not enough (seen in a live test).
_HOW_ASKED = re.compile(
    r"อยู่ที่ไหน|ที่ไหน|ตรงไหน|ทางไหน|ไปทางไหน|อยู่ไหน|ชั้นไหน|ยังไง|อย่างไร|อะไรบ้าง|อะไร|บ้าง|"
    r"กี่โมง|เท่าไหร่|เท่าไร|ได้ไหม|ไหม|มั้ย|หรือเปล่า|มีไหม|"
    r"มี|ได้|ต้อง|จะ|ขอ|อยาก|แล้ว|และ|หรือ|ของ|ให้|ใช้|คือ|เป็น|ที่นี่|ตรงนี้|บริการ|ทำ|ไป|อยู่|ที่"
)
_HOW_ASKED_EN = frozenset(
    "what where when how which who whom whose why is are am was were do does did can could "
    "would will should may i you we they he she it my your our their me us the a an to for "
    "of in on at by with there here this that these those any some please and or have has "
    "get find need want go time what's where's who's how's when's it's i'm".split()
)
MIN_SUBJECT_COVERAGE = 0.45  # share of the question's subject an entry must contain
MIN_ENTRY_PRESENT = 0.45  # or: share of the entry's own question found in what was said


@dataclass(frozen=True)
class RankedItem:
    item: KnowledgeItem
    score: float
    # The entry does not cover something the question asks: a topic it never
    # mentions, or none of the question's aspects (where / when / how much...).
    # It may still help the model, but must not be given as the answer by itself.
    partial: bool = False


def normalize(text: str) -> str:
    return _NOT_LETTERS.sub("", text.lower())


def trigrams(text: str) -> set[str]:
    text = normalize(text)
    if len(text) < 3:
        return {text} if text else set()
    return {text[i:i + 3] for i in range(len(text) - 2)}


@lru_cache(maxsize=8192)
def _sound_grams(text: str) -> frozenset[str]:
    """Trigrams of how ``text`` (as written) sounds.

    Remembered: the same entry texts are compared with every question, and working them out again for
    each one cost about 0.15 s per question with a few hundred entries (an answer is waited for).
    """
    key = concepts.sound_key(text)
    if len(key) < 3:
        return frozenset({key}) if key else frozenset()
    return frozenset(key[i:i + 3] for i in range(len(key) - 2))


def _without_fillers(text: str) -> str:
    """``text`` as written, minus polite particles and fillers (matched before marks are removed)."""
    return _FILLERS.sub(" ", text.lower())


def _core(text: str) -> str:
    """Normalised text without polite particles and fillers."""
    return normalize(_without_fillers(text))


def _overlap(question_grams: frozenset[str] | set[str], other_grams: frozenset[str] | set[str]) -> float:
    """Share of the question's trigrams that also occur in the other text (0–1)."""
    if not question_grams:
        return 0.0
    return len(question_grams & other_grams) / len(question_grams)


def find_exact(question: str, items: Sequence[KnowledgeItem]) -> KnowledgeItem | None:
    """The entry whose question is the same text (what the shortcut buttons send).

    Polite particles do not count: "เวลาทำการของวิทยาลัยครับ" is the same question.
    """
    wanted = normalize(question)
    if not wanted:
        return None
    core = _core(question)
    for item in items:
        if not item.enabled:
            continue
        for asked in (item.question, item.question_en):
            if asked and (normalize(asked) == wanted or (core and _core(asked) == core)):
                return item
    return None


def rank(question: str, items: Sequence[KnowledgeItem], limit: int | None = None) -> list[RankedItem]:
    """Enabled entries related to ``question``, best first."""
    plain = normalize(question)
    lower = question.lower()
    spoken = _without_fillers(question) if _core(question) else lower
    sound = concepts.sound_key(spoken)
    grams = _sound_grams(spoken)
    asked = concepts.find(question)
    ranked = []
    for item in items:
        if not item.enabled:
            continue
        keyword_hits = sum(1 for keyword in item.keywords if _keyword_in(keyword, lower, plain, sound))
        title_grams = _sound_grams(f"{item.question} {item.question_en} {item.category}")
        shared = len(grams & title_grams)
        # The entry's own question (Thai or English) found in what was said.
        entry_present = max(
            _overlap(_sound_grams(_without_fillers(asked)), grams)
            for asked in (item.question, item.question_en) if asked
        )
        question_overlap = max(shared / len(grams) if grams else 0.0, COVERAGE_FACTOR * entry_present)
        entry_text = (
            f"{item.question} {item.question_en} {item.category} {' '.join(item.keywords)} "
            f"{item.answer} {item.answer_en}"
        )
        # What an entry is about is in its question and keywords; a word in passing in its
        # answer ("รับยาใกล้จุดชำระเงิน") counts for less.
        titled = concepts.find(
            f"{item.question} {item.question_en} {item.category} {' '.join(item.keywords)}"
        )
        offered = titled | concepts.find(f"{item.answer} {item.answer_en}")
        subject_covered = _subject_coverage(question, entry_text)
        missing_topics = sum(1 for name in asked - offered if concepts.kind_of(name) == concepts.TOPIC)
        # "How do I..." is well served by where/when/whom to contact, so it never vetoes.
        asked_aspects = {
            name for name in asked
            if concepts.kind_of(name) == concepts.ASPECT and name not in concepts.LOOSE_ASPECTS
        }
        concept_score = sum(
            (TOPIC_WEIGHT if concepts.kind_of(name) == concepts.TOPIC else ASPECT_WEIGHT)
            * (1.0 if name in titled else IN_ANSWER_ONLY)
            for name in asked & offered
        ) - MISSING_TOPIC_PENALTY * missing_topics
        partial = (
            missing_topics > 0
            or bool(asked_aspects and not asked_aspects & offered)
            # the question's own subject is not in the entry, and the entry's question was not asked
            or (subject_covered < MIN_SUBJECT_COVERAGE and entry_present < MIN_ENTRY_PRESENT)
        )
        score = (
            KEYWORD_WEIGHT * keyword_hits
            + QUESTION_WEIGHT * question_overlap
            + ANSWER_WEIGHT * _overlap(grams, _sound_grams(f"{item.answer} {item.answer_en}"))
            + concept_score
        )
        if score >= MIN_RELATED_SCORE:
            ranked.append(RankedItem(item, score, partial))
    ranked.sort(key=lambda entry: entry.score, reverse=True)
    return ranked[:limit] if limit else ranked


def covers_subject(question: str, item: KnowledgeItem) -> bool:
    """The entry contains what ``question`` is about (True for a question with no subject)."""
    entry_text = (
        f"{item.question} {item.question_en} {item.category} {' '.join(item.keywords)} "
        f"{item.answer} {item.answer_en}"
    )
    return _subject_coverage(question, entry_text) >= MIN_SUBJECT_COVERAGE


def _subject_coverage(question: str, entry_text: str) -> float:
    """How much of what the question is about (its words minus how it is asked) the entry contains.

    Thai pieces count by their sound trigrams, so a misspelt subject still counts;
    an English word counts if the entry has it (in its keywords, typically).
    Concepts the entry also mentions count as covered ("tuition" ~ "ค่าเทอม").
    1.0 when the question has no subject of its own ("ที่ไหนครับ").
    """
    entry_lower = entry_text.lower()
    entry_grams = _sound_grams(entry_text)
    entry_concepts = concepts.find(entry_text)
    # English words that belong to a concept the entry also mentions ("public relations").
    shared_words = concepts.english_words(question, entry_concepts)
    total = covered = 0.0
    thai = _HOW_ASKED.sub(" ", _without_fillers(question))
    for word in re.findall(r"[a-z0-9]+(?:[-'][a-z0-9]+)*", thai):
        if word in _HOW_ASKED_EN or len(word) < 2:
            continue
        total += len(word)
        if word in entry_lower or word in shared_words:
            covered += len(word)
    # Split on spaces, Latin and digits only: \W would also split at Thai vowel marks.
    for piece in re.split(r"[a-z0-9\s]+", thai):
        key = concepts.sound_key(piece)
        if len(key) < 2:
            continue
        grams = _sound_grams(piece)
        total += len(key)
        share = 0.0
        if shared := concepts.find(piece) & entry_concepts:
            # A shared concept covers its own words only: in "ท้องสมุดเปิดกี่มง" (the
            # library, misheard) "เปิด" matches any opening hours, "ท้องสมุด" must match too.
            rest, cut = concepts.without_terms(piece, shared)
            rest_grams = _sound_grams(rest)
            share = cut + (1.0 - cut) * (_overlap(rest_grams, entry_grams) if rest_grams else 1.0)
        covered += len(key) * max(share, _overlap(grams, entry_grams))
    return covered / total if total else 1.0


def _keyword_in(keyword: str, lower: str, plain: str, sound: str) -> bool:
    """An Admin keyword in the question, as written or as it sounds (Latin keywords as whole words)."""
    word = normalize(keyword)
    if not word:
        return False
    if keyword.isascii():
        # "Wi-Fi" and "wifi", "check-in" and "check in" are the same word to a visitor.
        unhyphened = re.sub(r"(?<=[a-z0-9])-(?=[a-z0-9])", "", lower)
        phrase = r"[\s-]*".join(re.escape(part) for part in keyword.lower().replace("-", " ").split())
        pattern = rf"(?<![a-z0-9]){phrase}(?![a-z0-9])"
        return re.search(pattern, lower) is not None or re.search(pattern, unhyphened) is not None
    return word in plain or concepts.sound_key(keyword) in sound


def confident_match(question: str, items: Sequence[KnowledgeItem]) -> KnowledgeItem | None:
    """Best entry if it clearly answers the question; used when Gemini is unavailable."""
    ranked = rank(question, items, limit=3)
    for entry in ranked:
        # Below the top only while still close to it: an entry that covers the
        # question, under one that shares more words but misses its subject.
        if entry.score < max(MIN_CONFIDENT_SCORE, ranked[0].score * MIN_SHARE_OF_TOP):
            break
        if not entry.partial:
            return entry.item
    return None


CLEAR_SUBJECT_COVERAGE = 0.8  # share of the question's subject an entry must contain to answer it alone


def clear_match(question: str, items: Sequence[KnowledgeItem]) -> KnowledgeItem | None:
    """An entry that answers ``question`` by itself, sure enough to skip the AI.

    Stricter than ``confident_match`` (the fallback when the AI is unreachable):
    nearly all of what the question asks about must be in the entry, so
    "ผลงานนี้ใช้ไฟกี่วัตต์" is not given the "how does it work" entry.
    """
    item = confident_match(question, items)
    if item is None:
        return None
    entry_text = (
        f"{item.question} {item.question_en} {item.category} {' '.join(item.keywords)} "
        f"{item.answer} {item.answer_en}"
    )
    if _subject_coverage(question, entry_text) < CLEAR_SUBJECT_COVERAGE:
        return None
    # What is asked must be what the entry is about (its question and keywords), not a word in
    # passing in its answer: "AI คืออะไร" got the category-2 entry, whose answer says "ใช้ AI".
    title = f"{item.question} {item.question_en} {item.category} {' '.join(item.keywords)}"
    if _subject_coverage(question, title) < MIN_SUBJECT_COVERAGE:
        return None
    # Everything the question asks about must be in the entry, "how" included: "พลังงานแสงอาทิตย์
    # ทำงานยังไง" got the list of renewable energies. Such a question goes to the model, with the entry.
    asked = {name for name in concepts.find(question) if concepts.kind_of(name) == concepts.ASPECT}
    return item if asked <= concepts.find(entry_text) else None


def select_for_context(
    question: str, items: Sequence[KnowledgeItem], max_items: int, conversation: str | None = None
) -> list[KnowledgeItem]:
    """What to show the model: everything if the knowledge base is small, else the most related.

    ``conversation`` (a follow-up together with the question before it) adds the entries
    related to the whole exchange, after those related to the question itself, so a
    follow-up about something new ("Where do I pay?") still brings its own entry.
    """
    enabled = [item for item in items if item.enabled]
    if len(enabled) <= max_items:
        return enabled
    selected: list[KnowledgeItem] = []
    for text in (question, conversation) if conversation else (question,):
        for entry in rank(text, enabled, limit=max_items):
            if entry.item not in selected and len(selected) < max_items:
                selected.append(entry.item)
    return selected or enabled[:max_items]


DEMO_TAG = "[ข้อมูลตัวอย่าง]"
_DEMO_MARKS = ("ข้อมูลตัวอย่าง", "DEMO", "SAMPLE")


def is_demo(item: KnowledgeItem) -> bool:
    """Made-up example content (labelled so in its answer or category), not the place's facts."""
    text = f"{item.category} {item.answer}".upper()
    return any(mark in text for mark in _DEMO_MARKS)


def format_context(items: Sequence[KnowledgeItem], announcements: Sequence[str] = ()) -> str:
    """The place's data as plain text for the model's instructions; example data is tagged."""
    lines = []
    for number, item in enumerate(items, start=1):
        category = f" (หมวด: {item.category})" if item.category else ""
        demo = f"{DEMO_TAG} " if is_demo(item) else ""
        lines.append(f"[{number}]{category} {item.question}\n    {demo}{item.answer}")
        if item.question_en and item.answer_en:
            lines.append(f"    (English) {item.question_en}\n    {demo}{item.answer_en}")
    if announcements:
        lines.append("ประกาศปัจจุบัน:")
        lines.extend(f"- {text}" for text in announcements)
    return "\n".join(lines) if lines else "(ยังไม่มีข้อมูล)"
