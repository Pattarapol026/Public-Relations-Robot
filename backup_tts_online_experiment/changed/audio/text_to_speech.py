"""Text-to-speech: online neural voices first (``audio/edge_voice.py``), the voices on this computer as the fallback.

The voices on this computer: Thai with PyThaiTTS (verified with 0.8.0), English with a Windows voice.

Thai uses the library's default model, ``fastthaig2p`` (Kokoro-82M with Thai
grapheme-to-phoneme, ONNX on CPU, 24 kHz). The model is loaded once. Loading
downloads about 330 MB to ``~/.cache/fastthaig2p`` the first time, so ``load``
and ``synthesize`` are blocking and must run on a worker thread.

That model only knows Thai sounds: English comes out as Thai syllables ("welcome"
-> ``wen.kʰam``). English therefore goes to an en-US voice that ships with Windows
(System.Speech, reached through PowerShell because no COM package is installed).
"""
from __future__ import annotations

import logging
import math
import os
import re
import subprocess
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, replace
from pathlib import Path

import numpy as np

from audio import voice_polish
from audio.spoken_form import to_spoken_english, to_spoken_thai
from audio.edge_voice import EdgeVoice, EdgeVoiceError, OnlineGate
from audio.voice_cache import VoiceCache

logger = logging.getLogger(__name__)

# The model accepts about 510 phonemes per call; a Thai sentence of this many
# characters stays inside that (a 60-character sentence measured 141). Text full
# of numbers may not (140 characters of phone numbers measured 741): see _thai_audio.
MAX_CHUNK_CHARS = 140
# Synthesis takes about 17 ms per character on this computer (a 60-character piece took 1.07 s), and the first
# piece decides how soon the robot starts to speak, so it is short. Each later piece may be up to
# ``PIECE_GROWTH`` times as long as the one before it: its synthesis then still finishes while the
# previous one is being spoken (speech lasts about 65 ms per character), so the pieces join without a gap.
FIRST_CHUNK_CHARS = 32
PIECE_GROWTH = 3.0
# A word or two left alone sounds wrong: the model says a lone "ครับ" poorly.
MIN_TAIL_CHARS = 12
CACHE_ENTRIES = 64
# The English voice has no phoneme limit and its time hardly grows with length, so a
# sentence stays whole: being cut gave "...for" and "more details" as separate pieces.
ENGLISH_MAX_CHUNK_CHARS = 300
ENGLISH_VOICE_TIMEOUT_S = 20.0
# It pads each piece with about 0.9 s of silence; pauses are added when pieces are joined.
ENGLISH_SILENCE_LEVEL = 0.002
ENGLISH_EDGE_S = 0.03

LANG_THAI = "th"
LANG_ENGLISH = "en"

ENGINE_EDGE = "edge"
ENGINE_LOCAL = "local"
# A soft, sweet woman's voice that still says "ผม ... ครับ": the team found the man's voice too manly (8 Nov 2026).
THAI_VOICE = "th-TH-PremwadeeNeural"
ENGLISH_VOICE = "en-US-AvaNeural"  # caring and pleasant
# A voice may carry its own pitch: "th-TH-NiwatNeural@+25" is Niwat 25 Hz higher.
# Premwadee often fails on this service (about a third of the requests, each after 4 s of waiting), so she is
# asked in parallel, and if she still does not answer in time, Niwat says that answer (and the next ones for a while).
FALLBACK_VOICES = {"th-TH-PremwadeeNeural": "th-TH-NiwatNeural@+35", "en-US-EmmaNeural": "en-US-AvaNeural"}
FLAKY_VOICES = frozenset({"th-TH-PremwadeeNeural", "en-US-EmmaNeural"})
FALLBACK_VOICE_STICKS_S = 600.0  # the longest a stand-in stays if nothing checks on the chosen voice; a probe ends it sooner
PROBE_EVERY_S = 6.0
PROBE_TIMEOUT_S = 3.0
REQUESTS_FIRST = (0.0, 1.0)  # when requests go out for the first piece of an answer ...
REQUESTS_OTHER = (0.0, 2.0)
REQUESTS_FLAKY_FIRST = (0.0, 0.9)  # ... and for a voice that fails often: when it works it answers in under 1.5 s
REQUESTS_FLAKY_OTHER = (0.0, 1.2, 2.4)
# The first piece of an answer decides how soon the robot speaks: if the online voice has not begun to
# answer by then, this answer is spoken by the local voice (never a change of voice half way through).
EDGE_FIRST_PIECE_TIMEOUT_S = 2.2
EDGE_PIECE_TIMEOUT_S = 8.0
# A neural voice reads at a natural pace already: the speed set for the local voice (0.9 = 10% slower)
# moves it only this much of the way (0.9 -> -5%).
EDGE_SPEED_SCALE = 0.3  # 0.9 -> -3%: talking, not reading slowly
EDGE_PITCH_HZ = 0
# Pieces for the online voice: a short first piece for a quick start, then whole sentences, because the
# voice shapes its intonation over a sentence.
EDGE_FIRST_CHUNK_CHARS = 60
EDGE_MAX_CHUNK_CHARS = 220
EDGE_MIN_PIECE_CHARS = 14
EDGE_MIN_FIRST_CHARS = 24  # the first piece is at least this long (when there is more to say)
EDGE_GROUP_CHARS = 170  # later sentences are spoken together up to this long: fewer joins, a more even flow

_SENTENCE_END = re.compile(r"(?<=[.!?。…])\s+|\n+")
_UNSPEAKABLE = re.compile(r"https?://\S+|[*_#`<>|\[\]{}]+")
_SPACES = re.compile(r"\s+")
_THAI_LETTER = re.compile(r"[฀-๿]")
_LATIN_LETTER = re.compile(r"[A-Za-z]")
_THAI_RUN = re.compile(r"[฀-๿]+(?:\s+[฀-๿]+)*")
_SAYABLE = re.compile(r"\w")
# The Thai model turns closing punctuation into an extra sound ("ครับ." was heard as "ครับเลย").
_CLOSING_PUNCTUATION = re.compile(r"[\s.!?…,;:。]+$")
# The Thai model reads the dashes of 02-123-4567 as "minus" and the groups as amounts.
_PHONE_NUMBER = re.compile(r"(?<![\d.])0\d(?:[- ]?\d){7,8}(?![\d])")
# The English voice reads 08.00 as "zero eight point zero zero" (a price stays a number).
_DOTTED_TIME = re.compile(r"\b([01]\d|2[0-3])\.([0-5]\d)\b(?!\s*(?:baht|thb|usd|%|percent))", re.IGNORECASE)
# How the Thai voice should say terms it cannot read as written: it knows only Thai sounds,
# reads a dash between letters or digits as "ลบ" and spells abbreviations badly. For speech
# only; the screen keeps the written form. Longest first, so "TH Sarabun PSK" wins over "TH".
_SAY_AS: tuple[tuple[re.Pattern, str], ...] = tuple(
    (re.compile(pattern, re.IGNORECASE), spoken) for pattern, spoken in (
        (r"ว\s*-\s*สอศ\s*-\s*(\d)", r"วอ สอศอ \1"),
        (r"thaiinvention\.net", "ไทยอินเวนชันดอตเน็ต"),
        (r"\bTH\s*Sarabun\s*PSK\b", "ทีเอช สารบรรณ พีเอสเค"),
        (r"\bconcept\s+paper\b", "คอนเซ็ปต์เปเปอร์"),
        (r"\belectric\s+vehicle\b", "อิเล็กทริกวีฮิเคิล"),
        (r"\bWi-?Fi\b", "ไวไฟ"),
        (r"\bATM\b", "เอทีเอ็ม"),
        (r"\bEnglish\b", "อิงลิช"),
        (r"\bVAT\b", "แวต"),
        (r"\bRefund\b", "รีฟันด์"),
        (r"\bAPI\b", "เอพีไอ"),
        (r"\bModel\b", "โมเดล"),
        (r"\bPrompt\b", "พรอมต์"),
        (r"\bShelf\s+life\b", "เชลฟ์ไลฟ์"),
        (r"\bHealth\s*Care\b", "เฮลท์แคร์"),
        (r"\bGoogle\b", "กูเกิล"),
        (r"\bFacebook\b", "เฟซบุ๊ก"),
        # "ม.3" is read as letters; people say the school year
        (r"\bม\.\s*(\d)", r"มัธยมศึกษาปีที่ \1"),
        (r"\bPDF\b", "พีดีเอฟ"),
        (r"\bGPIO\b", "จีพีไอโอ"),
        (r"\bBCG\b", "บีซีจี"),
        (r"\bSDGs?\b", "เอสดีจี"),
        (r"\bAira\b", "ไอร่า"),
        (r"\bAI\b", "เอไอ"),
        (r"\bEV\b", "อีวี"),
        (r"\bdataset\b", "ดาต้าเซ็ต"),
        (r"\btrain\b", "เทรน"),
        (r"\btest\b", "เทสต์"),
        (r"\bsoftware\b", "ซอฟต์แวร์"),
        (r"\bhardware\b", "ฮาร์ดแวร์"),
        (r"\binput\b", "อินพุต"),
        (r"\boutput\b", "เอาต์พุต"),
        (r"\bcontent\b", "คอนเทนต์"),
        (r"\badmin\b", "แอดมิน"),
        (r"\bpoint\b", "พอยต์"),
        # (?!อ): a piece cut again after a too-long first try is not changed twice
        (r"สอศ(?!อ)\.?", "สอศอ"),
        (r"สอจ(?!อ)\.?", "สอจอ"),
        (r"ปวช\.?", "ปอวอชอ"),
        (r"ปวส\.?", "ปอวอสอ"),
    )
)
_THAI_DIGITS = dict(zip("0123456789", "ศูนย์ หนึ่ง สอง สาม สี่ ห้า หก เจ็ด แปด เก้า".split()))
_DOT_INSIDE_WORD = re.compile(r"\w\.\w")
_LATIN_ABBREVIATIONS = {"dr", "mr", "mrs", "ms", "prof", "vs", "st"}
_THAI_SHORT_ENDINGS = {"ค่ะ", "คะ", "นะ", "จ้ะ", "จ้า"}

# Plain text comes in on stdin (never inside the script), raw PCM goes out on stdout. The voice is a woman's, like the
# online one. Keep this script free of quotes and non-ASCII text, even in comments: PowerShell then hangs on its command line.
_ENGLISH_VOICE_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Speech
$reader = New-Object IO.StreamReader([Console]::OpenStandardInput(), (New-Object Text.UTF8Encoding $false))
$text = $reader.ReadToEnd()
$voice = New-Object System.Speech.Synthesis.SpeechSynthesizer
$culture = [Globalization.CultureInfo]::GetCultureInfo('en-US')
$voice.SelectVoiceByHints('Female', 'Adult', 0, $culture)
if (-not $voice.Voice.Culture.Name.StartsWith('en')) { exit 3 }
$voice.Rate = [int]$env:SPEECH_RATE
$format = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo([int]$env:SPEECH_SAMPLE_RATE, 'Sixteen', 'Mono')
$buffer = New-Object IO.MemoryStream
$voice.SetOutputToAudioStream($buffer, $format)
$voice.Speak($text)
$out = [Console]::OpenStandardOutput()
$buffer.WriteTo($out)
$out.Flush()
"""


class TextToSpeechError(Exception):
    """Model loading or synthesis failed; the message is safe to show on screen."""


@dataclass(frozen=True)
class TtsConfig:
    model: str = "fastthaig2p"
    speed: float = 0.9
    engine: str = "local"  # "auto" = the online voice while it works, "edge" = only that, "local" = this computer's
    thai_voice: str = THAI_VOICE
    english_voice: str = ENGLISH_VOICE
    pitch_hz: int = EDGE_PITCH_HZ
    cache_dir: str | None = None  # speech kept on disk; None = not kept
    cache_megabytes: float = 300.0


def speech_language(text: str) -> str:
    """Which voice suits this text: English only when Latin letters outnumber Thai ones.

    Decided from the text that will be spoken, not from the question, so the voice
    always matches what is said.
    """
    if len(_LATIN_LETTER.findall(text)) > len(_THAI_LETTER.findall(text)):
        return LANG_ENGLISH
    return LANG_THAI


# --- cues: the small sounds of a person (a chuckle, "hmm") and pauses, written into the text to be spoken ---

CUE_NAMES = ("chuckle", "laugh", "hmm", "aha", "wow", "sigh")
LANG_CUE = "cue"  # the "language" of a piece that is a sound, not words
LANG_PAUSE = "pause"  # ... and of one that is silence
DEFAULT_PAUSE_S = 0.4
_MARKUP = re.compile(r"\[(" + "|".join(CUE_NAMES) + r"|pause)(?::(\d+(?:\.\d+)?))?\]", re.IGNORECASE)


def strip_markup(text: str) -> str:
    """The text without cue and pause marks (what the screen shows)."""
    return _SPACES.sub(" ", _MARKUP.sub(" ", text)).strip()


def speech_items(text: str, engine: str = ENGINE_LOCAL) -> list[tuple[str, str]]:
    """``(value, language)`` in speaking order like ``speech_pieces``, plus the sounds and pauses written into the text.

    ``[chuckle]`` ``[laugh]`` ``[hmm]`` ``[aha]`` ``[wow]`` ``[sigh]`` become ``("chuckle", "cue")``; ``[pause]`` or
    ``[pause:0.6]`` becomes ``("0.6", "pause")``; a new line is only a space. Everything else is split into pieces for the voice, as ``speech_pieces`` does.
    """
    items: list[tuple[str, str]] = []

    text = re.sub(r"[ \t]*\n+[ \t]*", " ", text)  # a new line is not a pause: it is an ordinary space

    def add_words(chunk: str) -> None:
        if not _SAYABLE.search(chunk):
            return
        started = any(language not in (LANG_CUE, LANG_PAUSE) for _value, language in items)
        items.extend(speech_pieces(chunk, engine, short_first=not started))

    position = 0
    for mark in _MARKUP.finditer(text):
        add_words(text[position:mark.start()])
        name = mark.group(1).lower()
        if name == "pause":
            items.append((str(min(2.0, float(mark.group(2) or DEFAULT_PAUSE_S))), LANG_PAUSE))
        else:
            items.append((name, LANG_CUE))
        position = mark.end()
    add_words(text[position:])
    return items


def prepare_for_online_voice(text: str, language: str) -> str:
    """The text as the online voice should read it: its own intonation needs the question mark, so only a
    closing full stop or comma goes; terms it cannot read are respelled as for the local voice."""
    text = _SPACES.sub(" ", text).strip()
    if language == LANG_ENGLISH:
        return to_spoken_english(_DOTTED_TIME.sub(r"\1:\2", text))
    if not _is_abbreviation(text):
        text = re.sub(r"[\s.,;:。]+$", "", text)
    return to_spoken_thai(say_as_spoken(_say_phone_numbers(text)))


def english_voice_rate(speed: float) -> int:
    """The Windows voice's rate for a speed multiplier: -10..10, where 10 means 3 times as fast.

    Whole steps only: 0.90 becomes -1, measured at 0.92 of the normal speed.
    """
    return max(-10, min(10, round(10 * math.log(speed, 3))))


def _is_abbreviation(text: str) -> bool:
    """Whether the text ends in an abbreviation's dot (ดร. พ.ศ. Dr.), not a sentence's."""
    word = text.rsplit(" ", 1)[-1]
    if not word.endswith("."):
        return False
    core = word[:-1]
    if _DOT_INSIDE_WORD.search(core):
        return True
    if _THAI_LETTER.search(core):
        return len(core) <= 3 and core not in _THAI_SHORT_ENDINGS
    return core.lower() in _LATIN_ABBREVIATIONS


def _sentences(text: str) -> list[str]:
    sentences, start = [], 0
    for end in _SENTENCE_END.finditer(text):
        if not _is_abbreviation(text[start:end.start()]):
            sentences.append(text[start:end.start()])
            start = end.end()
    sentences.append(text[start:])
    return sentences


def say_as_spoken(text: str) -> str:
    """``text`` with the terms the Thai voice cannot read replaced by how they sound."""
    for pattern, spoken in _SAY_AS:
        text = pattern.sub(spoken, text)
    return text


# Hotlines (1669, 1111, 191, 199) are said digit by digit; "1,000 บาท" and years are amounts.
_HOTLINE = re.compile(
    r"(?<![\d,.])(?:1\d{3}|19\d)(?![\d,.]|\s*(?:บาท|คน|ปี|วัน|เมตร|กิโล|ชั่วโมง|นาที|ชิ้น|เครื่อง|ทีม|ห้อง))"
)


def _say_phone_numbers(text: str) -> str:
    """Phone numbers and hotlines digit by digit, as a person reads them."""
    def digits(number: re.Match) -> str:
        return " ".join(_THAI_DIGITS[digit] for digit in number.group() if digit.isdigit())

    return _HOTLINE.sub(digits, _PHONE_NUMBER.sub(digits, text))


def _word_tokens(text: str) -> list[str]:
    try:
        from pythainlp.tokenize import word_tokenize
    except ImportError:
        return []
    return word_tokenize(text, engine="newmm")


# Thai letters and marks, without the digits and ๆ itself (U+0E46).
_THAI_RUN_BEFORE_YAMOK = re.compile(r"([ก-ฺเ-ๅ็-๎]+)[ \t]*ๆ")


def say_mai_yamok(text: str) -> str:
    """``ๆ`` replaced by the one word before it: "ข้อมูลอื่นๆ" is read "ข้อมูลอื่นอื่น".

    PyThaiTTS repeats the whole run of Thai letters before ``ๆ``, and Thai has no spaces
    between words, so it read "ข้อมูลอื่นข้อมูลอื่น", the whole phrase twice.
    """
    def repeat_last_word(match: re.Match) -> str:
        run = match.group(1)
        words = [word for word in _word_tokens(run) if word.strip()]
        return run + (words[-1] if words else run)

    return _THAI_RUN_BEFORE_YAMOK.sub(repeat_last_word, text).replace("ๆ", "")


def _cut_point(text: str, limit: int) -> int:
    """Where to end a piece of at most ``limit`` characters: at a space, else between words."""
    cut = text.rfind(" ", 0, limit + 1)
    if cut > 0:
        return cut
    position = 0  # Thai often runs for a long way without a space
    for word in _word_tokens(text):
        if position + len(word) > limit:
            break
        position += len(word)
    return position or limit  # one unbroken word: cut it rather than fail


def speech_pieces(text: str, engine: str = ENGINE_LOCAL, short_first: bool = True) -> list[tuple[str, str]]:
    """``(piece, language)`` in speaking order, each piece short enough for its voice.

    A Thai answer stays in the Thai voice throughout, English terms included: changing
    voice in mid-sentence is harder to follow. An English answer may hold a Thai name,
    which the English voice would skip, so that name alone goes to the Thai voice.
    ``engine`` is the voice that will speak: the online one takes whole sentences. ``short_first`` keeps the
    first piece short for a quick start (not wanted for words that follow others).
    """
    online = engine == ENGINE_EDGE
    if speech_language(text) == LANG_THAI:
        if online:
            return [(piece, LANG_THAI) for piece in split_for_online_voice(text, short_first=short_first)]
        return [(piece, LANG_THAI) for piece in split_for_speech(
            text, first_max_chars=FIRST_CHUNK_CHARS if short_first else None)]
    result: list[tuple[str, str]] = []
    pieces = split_for_online_voice(text, ENGLISH_MAX_CHUNK_CHARS, short_first) if online \
        else split_for_speech(text, ENGLISH_MAX_CHUNK_CHARS, None)
    for piece in pieces:
        start = 0
        for run in _THAI_RUN.finditer(piece):
            result.append((piece[start:run.start()].strip(), LANG_ENGLISH))
            result.append((run.group(), LANG_THAI))
            start = run.end()
        result.append((piece[start:].strip(), LANG_ENGLISH))
    return [(part, language) for part, language in result if _SAYABLE.search(part)]


# Where NOT to cut: right after these words (they lead into the next one), or right before these (they end the clause).
_NO_CUT_AFTER = frozenset((
    "ที่", "ของ", "ใน", "กับ", "แก่", "ให้", "โดย", "จาก", "ถึง", "เพื่อ", "ว่า", "และ", "หรือ", "แต่", "ซึ่ง", "เวลา", "วันที่",
    "ชั้น", "อาคาร", "ห้อง", "คุณ", "นาย", "นาง", "นางสาว", "ดร.", "พ.ศ.", "น.", "เมื่อ", "ถ้า", "หาก", "เพราะ", "จน", "ต้อง",
    "ควร", "จะ", "ตั้งแต่", "ประมาณ", "ทุก", "แต่ละ", "เป็น", "คือ", "มี", "อยู่",
))
_NO_CUT_BEFORE = frozenset(("ครับ", "ค่ะ", "คะ", "นะ", "นะครับ", "นะคะ", "ด้วย", "เลย", "แล้ว", "ไหม", "บ้าง", "ได้", "อีก", "เท่านั้น"))
_CLAUSE_END = ("ครับ", "ค่ะ", "คะ", "นะครับ", "นะคะ", "เลย", "ด้วย", "แล้ว", "ไหม", "บ้างครับ", "ได้ครับ", "ได้ค่ะ")
_CLAUSE_START = frozenset(("และ", "หรือ", "แต่", "โดย", "ซึ่ง", "เพราะ", "ถ้า", "หาก", "ส่วน", "สำหรับ", "หลังจาก", "ก่อน", "ตั้งแต่"))
_LATIN_OR_DIGIT = re.compile(r"[A-Za-z0-9]")


def _best_clause_space(sentence: str, low: int, high: int, target: int) -> int:
    """The space in ``sentence`` between ``low`` and ``high`` where a person would breathe: after a clause that is finished
    ("...ครับ"), before "และ/แต่/โดย", never between a number and its unit, a title and a name, or a word and the one it
    leads into. 0 = no space fit for it."""
    best, best_score = 0, None
    for index in range(max(1, low), min(high, len(sentence) - 2) + 1):
        if sentence[index] != " ":
            continue
        before_word = sentence[:index].rsplit(" ", 1)[-1]
        after_word = sentence[index + 1:].split(" ", 1)[0]
        if _LATIN_OR_DIGIT.search(sentence[index - 1]) or _LATIN_OR_DIGIT.search(sentence[index + 1]):
            continue  # "ชั้น 2", "IT Support", "08.30 น."
        if before_word in _NO_CUT_AFTER or after_word in _NO_CUT_BEFORE:
            continue
        score = -abs(index - target) / 20.0
        if before_word.endswith(_CLAUSE_END):
            score += 3.0
        if after_word in _CLAUSE_START:
            score += 2.0
        if best_score is None or score > best_score:
            best, best_score = index, score
    return best


def _first_piece_cut(sentence: str) -> int:
    """Where the short first piece ends (see ``_best_clause_space``); 0 = do not cut: a long sentence with no good place
    to breathe is spoken whole, as one flow, rather than cut where a person would not stop."""
    return _best_clause_space(sentence, 20, EDGE_FIRST_CHUNK_CHARS + 25, EDGE_FIRST_CHUNK_CHARS - 15)


def _cut_long(sentence: str, max_chars: int) -> list[str]:
    """A long stretch with no full stop (Thai often has none): cut into pieces of about ``EDGE_GROUP_CHARS`` at the places a
    person would breathe; where there is none, at a space; where there is no space, between words."""
    parts: list[str] = []
    rest = sentence
    while len(rest) > EDGE_GROUP_CHARS:
        cut = _best_clause_space(rest, EDGE_GROUP_CHARS // 2, EDGE_GROUP_CHARS, EDGE_GROUP_CHARS - 40)
        if cut <= 0:
            cut = _cut_point(rest, min(max_chars, EDGE_GROUP_CHARS + 30))
        if cut <= 0 or cut >= len(rest) - EDGE_MIN_PIECE_CHARS:
            break
        parts.append(rest[:cut].strip())
        rest = rest[cut:].strip()
    parts.append(rest)
    return parts


def split_for_online_voice(text: str, max_chars: int = EDGE_MAX_CHUNK_CHARS, short_first: bool = True) -> list[str]:
    """Pieces for the online voice: a short first piece for a quick start, then the rest in as few long pieces as will do.

    A person does not stop between sentences as a reader does, and a neural voice shapes its intonation over a
    whole stretch: so after the first piece, sentences are put together up to ``EDGE_GROUP_CHARS`` and a piece
    ends only where a sentence does (or, for a sentence over ``max_chars``, at a space as above). The first piece
    is at least ``EDGE_MIN_FIRST_CHARS`` long (the voice needs a moment to make the next one) and is cut only
    at a place a person would breathe. A piece under ``EDGE_MIN_PIECE_CHARS`` joins the one before it.
    """
    text = _SPACES.sub(" ", _UNSPEAKABLE.sub(" ", text)).strip()
    sentences = [sentence.strip() for sentence in _sentences(text) if sentence.strip()]
    pieces: list[str] = []
    if short_first and sentences:
        first = sentences.pop(0)
        while sentences and len(first) < EDGE_MIN_FIRST_CHARS and len(first) + len(sentences[0]) <= EDGE_FIRST_CHUNK_CHARS + 60:
            first = f"{first} {sentences.pop(0)}"
        if len(first) > EDGE_FIRST_CHUNK_CHARS + 25:
            cut = _first_piece_cut(first)
            if 0 < cut < len(first) - EDGE_MIN_PIECE_CHARS:
                sentences.insert(0, first[cut:].strip())
                first = first[:cut].strip()
        pieces.append(first)
    current = ""
    for sentence in sentences:
        parts = _cut_long(sentence, max_chars) if len(sentence) > EDGE_GROUP_CHARS else [sentence]
        for part in parts:
            if current and len(current) + 1 + len(part) <= EDGE_GROUP_CHARS:
                current = f"{current} {part}"
            else:
                if current:
                    pieces.append(current)
                current = part
    if current:
        pieces.append(current)
    if len(pieces) > 1 and len(pieces[-1]) < EDGE_MIN_PIECE_CHARS and not pieces[-2].endswith(("?", "!")):
        pieces[-2:] = [f"{pieces[-2]} {pieces[-1]}"]
    return pieces


def split_for_speech(
    text: str, max_chars: int = MAX_CHUNK_CHARS, first_max_chars: int | None = FIRST_CHUNK_CHARS
) -> list[str]:
    """Split text into pieces short enough for the model, at natural pauses.

    Thai marks pauses with spaces rather than punctuation, so long sentences
    are further divided at spaces, or between words where there is no space. The
    first piece is kept shorter than the rest: synthesis time grows with length,
    and the first piece decides how soon the robot starts to speak.
    """
    text = _SPACES.sub(" ", _UNSPEAKABLE.sub(" ", text)).strip()
    chunks: list[str] = []
    for sentence in _sentences(text):
        sentence = sentence.strip()
        while sentence:
            if first_max_chars is None:
                limit = max_chars
            elif not chunks:
                limit = min(max_chars, first_max_chars)
            else:
                limit = min(max_chars, max(first_max_chars, int(PIECE_GROWTH * len(chunks[-1]))))
            if len(sentence) <= limit:
                chunks.append(sentence)
                break
            cut = _cut_point(sentence, limit)
            if len(sentence[cut:].strip()) < MIN_TAIL_CHARS:
                # Leave the last words some company rather than a piece of their own.
                cut = _cut_point(sentence, cut - 1) or cut
            chunks.append(sentence[:cut].strip())
            sentence = sentence[cut:].strip()
    return chunks


class TextToSpeechService:
    def __init__(self, config: TtsConfig, edge: EdgeVoice | None = None) -> None:
        self._config = config
        self._tts = None
        self._lock = threading.Lock()  # the local voices: one at a time
        self._edge = edge if edge is not None else (EdgeVoice() if config.engine != ENGINE_LOCAL else None)
        self._gate = OnlineGate()
        self._disk = VoiceCache(Path(config.cache_dir), config.cache_megabytes) if config.cache_dir else None
        self._memory_lock = threading.Lock()  # the online voice's pieces in memory (its synthesis is not under it)
        self._edge_cache: OrderedDict[str, np.ndarray] = OrderedDict()
        self._probing: set[str] = set()
        self._closed = False
        self._stand_in: dict[str, tuple[str, float, str]] = {}  # language -> (voice in its place, until, the voice it stands in for)
        # Greetings and common answers are spoken again and again.
        self._cache: OrderedDict[tuple[str, float, str], np.ndarray] = OrderedDict()

    @property
    def speed(self) -> float:
        return self._config.speed

    def set_speed(self, speed: float) -> None:
        """Speaking speed for the next pieces: a rate, so 0.9 lasts about 1.09 times as long."""
        self._config = replace(self._config, speed=speed)

    @property
    def config(self) -> TtsConfig:
        return self._config

    def set_voices(self, thai: str | None = None, english: str | None = None) -> None:
        """Other online voices for the next answers (the Admin's choice)."""
        self._config = replace(
            self._config, thai_voice=thai or self._config.thai_voice, english_voice=english or self._config.english_voice
        )

    @property
    def voice_names(self) -> tuple[str, str]:
        """The online voices in use: (Thai, English)."""
        return self._config.thai_voice, self._config.english_voice

    @property
    def engine_text(self) -> str:
        """Where speech comes from right now, for the Admin."""
        if not self.online_available:
            return "เสียงในเครื่อง (ไม่ใช้อินเทอร์เน็ต)"
        if self.plan_engine() == ENGINE_EDGE:
            return "เสียงประสาทออนไลน์ (ถ้าอินเทอร์เน็ตหลุดจะใช้เสียงในเครื่องแทนเอง)"
        return "ตอนนี้ใช้เสียงในเครื่อง เพราะเสียงออนไลน์ใช้ไม่ได้ชั่วคราว (จะลองใหม่เอง)"

    @property
    def online_available(self) -> bool:
        return self._edge is not None and self._config.engine != ENGINE_LOCAL

    def plan_engine(self) -> str:
        """The voice for one whole answer: the online one while it works, else this computer's."""
        if self.online_available and (self._config.engine == ENGINE_EDGE or self._gate.allows()):
            return ENGINE_EDGE
        return ENGINE_LOCAL

    @property
    def is_loaded(self) -> bool:
        return self._tts is not None

    @property
    def sample_rate(self) -> int:
        """Output sample rate in Hz; known once the model is loaded."""
        return int(self._tts.model.sample_rate) if self._tts is not None else 24_000

    def load(self) -> None:
        """Load the model if it is not loaded yet. Safe to call repeatedly."""
        with self._lock:
            self._load_locked()

    def synthesize(
        self, text: str, speed: float | None = None, language: str | None = None, engine: str | None = None,
        first: bool = False,
    ) -> np.ndarray:
        """Speech for one short piece of text, as mono float32 at ``sample_rate``.

        Pass pieces from ``speech_pieces``. ``speed`` overrides the configured speed
        (the Admin's "try it" button); ``language`` picks the voice, by default from the text.
        """
        return self.synthesize_piece(text, speed, language, engine, first)[0]

    def synthesize_piece(
        self, text: str, speed: float | None = None, language: str | None = None, engine: str | None = None,
        first: bool = False,
    ) -> tuple[np.ndarray, str]:
        """``(audio, engine that made it)``. ``engine`` (from ``plan_engine``) keeps an answer in one voice;
        if the online voice fails, this piece comes from the local one and the answer is told so."""
        text = text.strip()
        if not text:
            return np.zeros(0, dtype=np.float32), engine or ENGINE_LOCAL
        speed = self._config.speed if speed is None else speed
        language = language or speech_language(text)
        engine = engine or self.plan_engine()
        if engine == ENGINE_EDGE and self._edge is not None:
            try:
                return self._edge_audio(text, speed, language, first), ENGINE_EDGE
            except EdgeVoiceError as exc:
                self._gate.failure()
                logger.warning("Online voice failed (%s): speaking this one with the voice on this computer", exc)
        return self._local_audio(text, speed, language), ENGINE_LOCAL

    # --- the online voice ---

    def prepare_piece(self, text: str, language: str, attempts: int = 4) -> bool:
        """Make ``text`` in the chosen voice and keep it (memory and disk), trying again when the voice fails: this is
        preparation, nobody is waiting, and a stand-in voice must not be kept (or remembered) for it."""
        voice = self._config.thai_voice if language == LANG_THAI else self._config.english_voice
        for attempt in range(attempts):
            try:
                self._edge_audio(text, self._config.speed, language, False, voice=voice)
                return True
            except EdgeVoiceError:
                time.sleep(1.0 + attempt)
        return False

    def _voice_for(self, language: str) -> str:
        """The online voice to try first for ``language``: the Admin's, or the stand-in that has taken over for a while."""
        chosen = self._config.thai_voice if language == LANG_THAI else self._config.english_voice
        stand_in = self._stand_in.get(language)
        if stand_in is not None and time.monotonic() < stand_in[1] and stand_in[2] == chosen:
            return stand_in[0]
        return chosen

    def _edge_audio(self, text: str, speed: float, language: str, first: bool, voice: str | None = None) -> np.ndarray:
        """Speech for a piece from the online voice. If the chosen voice fails, its fallback voice says it (and the next
        pieces for a while, so one answer does not swap voices back and forth); only if that fails too does this raise."""
        chosen = voice or self._voice_for(language)
        try:
            return self._edge_audio_with(text, speed, language, first, chosen)
        except EdgeVoiceError as exc:
            name = chosen.split("@")[0]
            fallback = FALLBACK_VOICES.get(name) if voice is None else None
            if fallback is None or fallback == chosen:
                raise
            logger.warning("Online voice %s failed (%s): %s says this one", chosen, exc, fallback)
            audio = self._edge_audio_with(text, speed, language, True, fallback)  # (short time limit: someone is waiting)
            home = self._config.thai_voice if language == LANG_THAI else self._config.english_voice
            self._stand_in[language] = (fallback, time.monotonic() + FALLBACK_VOICE_STICKS_S, home)
            self._start_probe(language, home)
            return audio

    def _start_probe(self, language: str, home: str) -> None:
        """While a stand-in voice is speaking, ask the chosen voice for one short word now and then; when it answers twice in a
        row it takes over again. Answers never wait to find out (measured 8 Nov: the woman's Thai voice answers 20 of 20
        for minutes, then 0 of 3, with no pattern; the man's voice never failed in 90 requests)."""
        with self._memory_lock:
            if language in self._probing:
                return
            self._probing.add(language)
        threading.Thread(target=self._probe, args=(language, home), name="voice-probe", daemon=True).start()

    def _probe(self, language: str, home: str) -> None:
        name = home.split("@")[0]
        word = "สวัสดีครับ" if language == LANG_THAI else "Hello."
        good = 0
        try:
            while time.monotonic() < self._stand_in.get(language, ("", 0.0, ""))[1] and not self._closed:
                time.sleep(PROBE_EVERY_S)
                try:
                    self._edge.synthesize(word, name, 0, 0, timeout_s=PROBE_TIMEOUT_S, starts=(0.0,))
                    good += 1
                except EdgeVoiceError:
                    good = 0
                if good >= 2:
                    self._stand_in.pop(language, None)
                    logger.info("Online voice %s answers again: it takes over", home)
                    return
        finally:
            with self._memory_lock:
                self._probing.discard(language)

    def close(self) -> None:
        self._closed = True

    def _edge_audio_with(self, text: str, speed: float, language: str, first: bool, voice: str) -> np.ndarray:
        name, _, own_pitch = voice.partition("@")
        pitch = int(own_pitch) if own_pitch else self._config.pitch_hz
        rate = round((speed - 1.0) * 100 * EDGE_SPEED_SCALE)
        key = VoiceCache.key(ENGINE_EDGE, voice, rate, pitch, text)
        with self._memory_lock:
            cached = self._edge_cache.get(key)
            if cached is not None:
                self._edge_cache.move_to_end(key)
                return cached
        audio = self._disk.get(key) if self._disk is not None else None
        if audio is None:
            started = time.monotonic()
            spoken = prepare_for_online_voice(text, language)
            flaky = name in FLAKY_VOICES
            starts = (REQUESTS_FLAKY_FIRST if flaky else REQUESTS_FIRST) if first else (
                REQUESTS_FLAKY_OTHER if flaky else REQUESTS_OTHER)
            raw = self._edge.synthesize(
                spoken, name, rate, pitch,
                timeout_s=EDGE_FIRST_PIECE_TIMEOUT_S if first else EDGE_PIECE_TIMEOUT_S, starts=starts,
            )
            self._gate.success()
            before = voice_polish.longest_pause(raw)
            audio = voice_polish.polish(raw)
            logger.info(
                "Online voice %s: %.1f s of speech in %.2f s, longest pause inside %.2f -> %.2f s (%d characters)",
                voice, len(audio) / self.sample_rate, time.monotonic() - started,
                before, voice_polish.longest_pause(audio), len(text),
            )
            if self._disk is not None:
                self._disk.put(key, audio)
        with self._memory_lock:
            self._edge_cache[key] = audio
            while len(self._edge_cache) > CACHE_ENTRIES:
                self._edge_cache.popitem(last=False)
        return audio

    # --- the voices on this computer ---

    def _local_audio(self, text: str, speed: float, language: str) -> np.ndarray:
        key = (text, speed, language)
        with self._lock:
            cached = self._cache.get(key)
            if cached is not None:
                self._cache.move_to_end(key)
                return cached
            tts = self._load_locked()
            started = time.monotonic()
            if language == LANG_ENGLISH:
                audio = self._english_audio(text, speed)
            else:
                try:
                    audio = self._thai_audio(tts, text, speed)
                except Exception as exc:
                    logger.exception("Speech synthesis failed")
                    raise TextToSpeechError("สร้างเสียงพูดไม่สำเร็จ") from exc
            audio = np.ascontiguousarray(np.asarray(audio, dtype=np.float32).reshape(-1))
            audio = voice_polish.polish(audio, self.sample_rate)
            logger.debug(
                "Synthesised %.1f s of speech in %.1f s",
                len(audio) / self.sample_rate, time.monotonic() - started,
            )
            self._cache[key] = audio
            while len(self._cache) > CACHE_ENTRIES:
                self._cache.popitem(last=False)
            return audio

    def _thai_audio(self, tts, text: str, speed: float) -> np.ndarray:
        if not _is_abbreviation(text):
            text = _CLOSING_PUNCTUATION.sub("", text)
        if not text:
            return np.zeros(0, dtype=np.float32)
        text = say_as_spoken(_say_phone_numbers(say_mai_yamok(text)))
        try:
            return tts.tts(text, return_type="waveform", speed=speed)
        except ValueError:  # too many phonemes for one call
            cut = _cut_point(text, len(text) // 2)
            if not 0 < cut < len(text):
                raise
        return np.concatenate((
            np.asarray(self._thai_audio(tts, text[:cut].strip(), speed), dtype=np.float32).reshape(-1),
            np.asarray(self._thai_audio(tts, text[cut:].strip(), speed), dtype=np.float32).reshape(-1),
        ))

    def _english_audio(self, text: str, speed: float) -> np.ndarray:
        environment = {
            **os.environ, "SPEECH_RATE": str(english_voice_rate(speed)),
            "SPEECH_SAMPLE_RATE": str(self.sample_rate),
        }
        try:
            done = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", _ENGLISH_VOICE_SCRIPT],
                input=_DOTTED_TIME.sub(r"\1:\2", text).encode("utf-8"), capture_output=True, env=environment,
                timeout=ENGLISH_VOICE_TIMEOUT_S, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except (OSError, subprocess.SubprocessError) as exc:
            logger.exception("The English voice could not run")
            raise TextToSpeechError("สร้างเสียงภาษาอังกฤษไม่สำเร็จ") from exc
        if done.returncode != 0 or not done.stdout:
            logger.error(
                "The English voice failed (exit %d): %s",
                done.returncode, done.stderr.decode("utf-8", "replace")[:300],
            )
            raise TextToSpeechError("สร้างเสียงภาษาอังกฤษไม่สำเร็จ (ไม่พบเสียงภาษาอังกฤษของ Windows)")
        audio = np.frombuffer(done.stdout, dtype=np.int16).astype(np.float32) / 32768.0
        sound = np.flatnonzero(np.abs(audio) > ENGLISH_SILENCE_LEVEL)
        if len(sound) == 0:
            return audio[:0]
        edge = int(ENGLISH_EDGE_S * self.sample_rate)
        return audio[max(0, sound[0] - edge):sound[-1] + 1 + edge]

    def _load_locked(self):
        if self._tts is not None:
            return self._tts
        try:
            from pythaitts import TTS
        except ImportError as exc:
            raise TextToSpeechError("ยังไม่ได้ติดตั้ง PyThaiTTS") from exc
        logger.info("Loading speech synthesis model %s", self._config.model)
        started = time.monotonic()
        try:
            self._tts = TTS(pretrained=self._config.model)
        except Exception as exc:
            logger.exception("Could not load speech synthesis model %s", self._config.model)
            raise TextToSpeechError(
                "โหลดโมเดลเสียงพูดไม่สำเร็จ (ครั้งแรกต้องเชื่อมต่ออินเทอร์เน็ตเพื่อดาวน์โหลดโมเดล)"
            ) from exc
        _word_tokens("สวัสดี")  # loads the dictionary here: splitting runs on the UI thread
        logger.info("Speech synthesis model loaded in %.1f s", time.monotonic() - started)
        return self._tts
