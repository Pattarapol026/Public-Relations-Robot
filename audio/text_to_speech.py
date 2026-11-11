"""Text-to-speech: Thai with PyThaiTTS (verified with 0.8.0), English with a Windows voice.

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

import numpy as np

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
        (r"\bolive\s+ridley\b", "โอลิฟริดลีย์"),
        (r"\bT-?VET\b", "ทีเวท"),
        (r"\bA4\b", "เอสี่"),
        (r"(?<![\d.])933(?![\d.])", "เก้าสามสาม"),
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
        (r"อศจ\.?", "ออ ศอ จอ"),
        (r"ปวช\.?", "ปอวอชอ"),
        (r"ปวส\.?", "ปอวอสอ"),
    )
)
# Words the speech model's dictionary lacks: the library then spells them letter by letter ("ชม" came out as
# "ชอ-มอ", the "ชอ" in "ปอวอชอ" gained a stray "อะ", a lone "อ" became "อะ"). IPA written the way the library's table writes it, between slashes.
_IPA_FIXES = {
    "ชม": "/t͡ɕʰom˧/",
    "ชอ": "/t͡ɕʰɔː˧/",
    "ช": "/t͡ɕʰɔː˧/",
    "อ": "/ʔɔː˧/",
    # the user asked for "ท่าน" short and crisp, "ทั่น" (the long "ท่านน" dragged at the end of a line)
    "ท่าน": "/tʰan˥˩/",
    # other entries the dictionary had wrong (short "a" where the word has a long one, missing syllables)
    "ทุกท่าน": "/tʰuk̚˦˥.tʰan˥˩/",
    "บางท่าน": "/baːŋ˧.tʰan˥˩/",
    "ท่านผู้หญิง": "/tʰan˥˩.pʰuː˥˩.jiŋ˩˩˦/",
    "บ้าง": "/baːŋ˥˩/",
    "ยกตัวอย่าง": "/jok̚˦˥.tua̯˧.jaːŋ˨˩/",
    "ผู้ว่าการ": "/pʰuː˥˩.waː˥˩.kaːn˧/",
    "การวิวาท": "/kaːn˧.wi˦˥.waːt̚˥˩/",
    "การสืบทอด": "/kaːn˧.sɯːp̚˨˩.tʰɔːt̚˥˩/",
}


def apply_pronunciation_fixes(tts) -> bool:
    """Teach the loaded speech model the words in ``_IPA_FIXES``. False if its internals are not as expected."""
    table = getattr(getattr(getattr(tts, "model", None), "_g2p", None), "_ipa", None)
    if not isinstance(table, dict):
        return False
    table.update(_IPA_FIXES)
    return True


_THAI_DIGITS = dict(zip("0123456789", "ศูนย์ หนึ่ง สอง สาม สี่ ห้า หก เจ็ด แปด เก้า".split()))
_DOT_INSIDE_WORD = re.compile(r"\w\.\w")
_LATIN_ABBREVIATIONS = {"dr", "mr", "mrs", "ms", "prof", "vs", "st"}
_THAI_SHORT_ENDINGS = {"ค่ะ", "คะ", "นะ", "จ้ะ", "จ้า"}

# Plain text comes in on stdin (never inside the script), raw PCM goes out on stdout.
_ENGLISH_VOICE_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Speech
$reader = New-Object IO.StreamReader([Console]::OpenStandardInput(), (New-Object Text.UTF8Encoding $false))
$text = $reader.ReadToEnd()
$voice = New-Object System.Speech.Synthesis.SpeechSynthesizer
$voice.SelectVoiceByHints('Female', 'Adult', 0, [Globalization.CultureInfo]::GetCultureInfo('en-US'))
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


def speech_language(text: str) -> str:
    """Which voice suits this text: English only when Latin letters outnumber Thai ones.

    Decided from the text that will be spoken, not from the question, so the voice
    always matches what is said.
    """
    if len(_LATIN_LETTER.findall(text)) > len(_THAI_LETTER.findall(text)):
        return LANG_ENGLISH
    return LANG_THAI


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


_CLOCK = r"([01]?\d|2[0-3])\.([0-5]\d)(?!\d)"
_CLOCK_RANGE = re.compile(rf"(?<![\d.]){_CLOCK}(\s*(?:ถึง|-)\s*){_CLOCK}\s*น\.")
_CLOCK_ALONE = re.compile(rf"(?<![\d.]){_CLOCK}\s*น\.")


def _clock(hours: str, minutes: str) -> str:
    return f"{int(hours)} นาฬิกา" + (f" {int(minutes)} นาที" if int(minutes) else "")


def say_clock_times(text: str) -> str:
    """"13.00 ถึง 14.00 น." and "09.30 น." as the Thai voice should say them ("13 นาฬิกา ถึง 14 นาฬิกา"): left as written
    it reads "สิบสามจุดศูนย์ศูนย์". Only times that end in "น." are changed, so a price like 3.50 stays a number."""
    text = _CLOCK_RANGE.sub(
        lambda m: f"{_clock(m.group(1), m.group(2))} ถึง {_clock(m.group(4), m.group(5))}", text)
    return _CLOCK_ALONE.sub(lambda m: _clock(m.group(1), m.group(2)), text)


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


def speech_pieces(text: str) -> list[tuple[str, str]]:
    """``(piece, language)`` in speaking order, each piece short enough for its voice.

    A Thai answer stays in the Thai voice throughout, English terms included: changing
    voice in mid-sentence is harder to follow. An English answer may hold a Thai name,
    which the English voice would skip, so that name alone goes to the Thai voice.
    """
    if speech_language(text) == LANG_THAI:
        return [(piece, LANG_THAI) for piece in split_for_speech(text)]
    result: list[tuple[str, str]] = []
    for piece in split_for_speech(text, ENGLISH_MAX_CHUNK_CHARS, None):
        start = 0
        for run in _THAI_RUN.finditer(piece):
            result.append((piece[start:run.start()].strip(), LANG_ENGLISH))
            result.append((run.group(), LANG_THAI))
            start = run.end()
        result.append((piece[start:].strip(), LANG_ENGLISH))
    return [(part, language) for part, language in result if _SAYABLE.search(part)]


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
    def __init__(self, config: TtsConfig) -> None:
        self._config = config
        self._tts = None
        self._lock = threading.Lock()
        # Greetings and common answers are spoken again and again.
        self._cache: OrderedDict[tuple[str, float, str], np.ndarray] = OrderedDict()

    @property
    def speed(self) -> float:
        return self._config.speed

    def set_speed(self, speed: float) -> None:
        """Speaking speed for the next pieces: a rate, so 0.9 lasts about 1.09 times as long."""
        self._config = replace(self._config, speed=speed)

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

    def synthesize(self, text: str, speed: float | None = None, language: str | None = None) -> np.ndarray:
        """Speech for one short piece of text, as mono float32 at ``sample_rate``.

        Pass pieces from ``speech_pieces``. ``speed`` overrides the configured speed
        (the Admin's "try it" button); ``language`` picks the voice, by default from the text.
        """
        text = text.strip()
        if not text:
            return np.zeros(0, dtype=np.float32)
        speed = self._config.speed if speed is None else speed
        language = language or speech_language(text)
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
        text = say_as_spoken(say_clock_times(_say_phone_numbers(say_mai_yamok(text))))
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
        if not apply_pronunciation_fixes(self._tts):
            logger.warning("Could not apply the pronunciation fixes: the speech model looks different")
        _word_tokens("สวัสดี")  # loads the dictionary here: splitting runs on the UI thread
        logger.info("Speech synthesis model loaded in %.1f s", time.monotonic() - started)
        return self._tts
