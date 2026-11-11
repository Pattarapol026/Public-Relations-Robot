"""Speech-to-text with faster-whisper (verified against faster-whisper 1.2.1).

The models are loaded once and reused. Loading may download them from Hugging
Face the first time, so both ``load`` and ``transcribe`` are blocking and must
run on a worker thread.

Thai goes to a Whisper model trained for Thai (``thai_model``, Thonburian
Whisper medium), which hears Thai far better than the general ``model``:
"ค่าเทอม" rather than "ข้าเทิม". Which language was spoken is decided first by
the small, fast ``detector_model``; English stays on the general model as before.
Without the Thai model (not on disk, or switched off) everything works as it did.
"""
from __future__ import annotations

import logging
import os
import re
import threading
import time
import wave
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import numpy as np

from audio.cuda_support import cuda_available, register_cuda_libraries

logger = logging.getLogger(__name__)

SAMPLE_RATE = 16_000
MIN_AUDIO_SECONDS = 0.3

# Quiet recordings (a laptop microphone at arm's length) are raised to this
# peak before the voice-activity filter, which otherwise drops soft speech.
TARGET_PEAK = 0.5
MAX_GAIN = 20.0

# Languages a visitor may speak when the configured language is Thai or
# English: the result is one of these two, so an English question is not
# forced into Thai script and a short Thai one is not transcribed as Lao.
SPOKEN_LANGUAGES = ("th", "en")
# How sure the detector must be that it heard English (see ``_thai_or_english``).
MIN_ENGLISH_PROB = 0.5
MIN_THAI_PROB = 0.5
# Neither is clear (an accent, a short phrase, a word of the other language): the speech is transcribed both ways and
# the transcript with the better evidence wins (``choose_transcript``). Only when English has some support and the
# two together are more than room noise (measured: noise scores 0.17-0.20 English and 0.01 Thai).
UNSURE_MIN_ENGLISH = 0.15
UNSURE_MIN_EVIDENCE = 0.25
# The English transcript must beat the Thai one by this much (mean log-probability) to be chosen: most visitors speak Thai.
ENGLISH_MARGIN = 0.10
MIN_ENGLISH_WORDS = 2
UNSURE = "unsure"
NO_SCORE = -9.0
# Whisper's well-known inventions on silence or noise (it learned them from subtitles).
_HALLUCINATIONS = (
    "ขอบคุณที่รับชม", "ขอบคุณสําหรับการรับชม", "ขอบคุณสำหรับการรับชม", "ซับไตเติ้ล", "subtitles by",
    "thank you for watching", "thanks for watching", "please subscribe", "amara.org", "กดติดตาม",
    "comments section below",
)
# Segments the model itself thinks are not speech (the rule Whisper uses).
NO_SPEECH_PROB = 0.6
LOW_LOGPROB = -1.0
# Beam width of the Thai model. On the processor a wide beam cost a second more for almost no
# gain, so it decoded greedily; on the graphics card the wider beam costs a tenth of a second and
# is a little more exact.
CPU_THAI_BEAM = 1
GPU_THAI_BEAM = 5
CPU_COMPUTE_TYPE = "int8"


class SpeechToTextError(Exception):
    """Model loading or transcription failed; the message is safe to show on screen."""


def _env_prompt() -> str:
    return os.environ.get("STT_THAI_PROMPT", "").strip()


def _env_audio_dir() -> Path | None:
    """STT_SAVE_AUDIO=<folder>: keep every turn's audio and transcript there (to build a real test set). Off by default."""
    value = os.environ.get("STT_SAVE_AUDIO", "").strip()
    return Path(value) if value else None


@dataclass(frozen=True)
class SttConfig:
    model: str = "small"
    device: str = "cpu"
    compute_type: str = "int8"
    language: str = "th"
    model_dir: Path | None = None
    thai_model: str = ""  # "" = Thai uses ``model`` too
    thai_model_revision: str | None = None
    detector_model: str = "tiny"  # decides Thai or English before the Thai model is used
    # Words to bias the Thai model towards (STT_THAI_PROMPT). Off by default: a fine-tuned model can repeat it on noise,
    # so it is only to be switched on after it has been compared with and without on real recordings.
    thai_prompt: str = field(default_factory=_env_prompt)
    save_audio_dir: Path | None = field(default_factory=_env_audio_dir)


class SpeechToTextService:
    def __init__(self, config: SttConfig) -> None:
        self._config = config
        self._model = None
        self._thai = None  # the Thai model, once loaded; None = not used
        self._detector = None
        # One lock for loading and for inference: the models are not used concurrently.
        self._lock = threading.Lock()
        self._cancelled: Callable[[], bool] | None = None  # of the job that holds the lock
        self._device = ""  # where the models really run ("cuda" or "cpu"), known once loaded
        self._compute_type = ""
        self._quality: list[tuple[float, float]] = []  # (avg_logprob, no_speech_prob) of the segments kept, this job

    @property
    def is_loaded(self) -> bool:
        return self._model is not None

    @property
    def uses_thai_model(self) -> bool:
        return self._thai is not None

    @property
    def config(self) -> SttConfig:
        return self._config

    @property
    def device(self) -> str:
        """Where the models run now: "cuda" (the graphics card), "cpu", or "" before they are loaded."""
        return self._device

    @property
    def engine_text(self) -> str:
        """For the Admin: the models and where they run."""
        config = self._config
        names = config.thai_model.split("/")[-1] if config.thai_model else config.model
        where = {"cuda": "การ์ดจอ NVIDIA", "cpu": "ซีพียู"}.get(self._device, config.device)
        return f"faster-whisper {names} ({where}, ภาษา {config.language})"

    def load(self) -> None:
        """Load the model if it is not loaded yet. Safe to call repeatedly."""
        with self._lock:
            self._load_locked()
            self._warm_up_locked()

    def transcribe(
        self, audio: np.ndarray, language: str | None = None, cancelled: Callable[[], bool] | None = None,
    ) -> str:
        """Transcribe mono float32 audio sampled at 16 kHz. Returns '' for silence.

        ``cancelled`` says the result is no longer wanted (the visitor spoke on, or stopped). A
        job that has not started when it turns true is skipped, and one that has stops between
        its steps. Jobs wait for each other, so an unwanted one would otherwise hold up the one
        that matters for as long as it takes to run (seconds each, on this computer).

        ``language`` overrides the configured language for this call (the
        visitor chose English on screen); "auto" lets the model detect it.
        Without an override, a configured Thai or English means "Thai or
        English, whichever was spoken".
        """
        if audio.ndim != 1:
            raise SpeechToTextError("รูปแบบเสียงไม่ถูกต้อง")
        if len(audio) < SAMPLE_RATE * MIN_AUDIO_SECONDS:
            return ""
        audio = normalize_level(np.ascontiguousarray(audio, dtype=np.float32))
        if cancelled is not None and cancelled():
            return ""
        started = time.monotonic()
        with self._lock:
            if cancelled is not None and cancelled():
                return ""  # it waited for the lock, and by then nobody wanted it
            self._cancelled = cancelled
            self._quality = []
            model = self._load_locked()
            try:
                chosen = self._language(language) if language is not None else None  # by the visitor
                if self._thai is not None and (self._bilingual(language) or chosen == "th"):
                    text = self._transcribe_with_thai_model(model, audio, chosen)
                elif self._bilingual(language):
                    text = self._transcribe_thai_or_english(model, audio)
                else:
                    text = self._collect(self._start(model, audio, self._language(language)))
            except Exception as exc:
                logger.exception("Transcription failed")
                raise SpeechToTextError("ถอดเสียงเป็นข้อความไม่สำเร็จ") from exc
            finally:
                self._cancelled = None
        logger.info(
            "Transcribed %.1f s of audio in %.1f s (%d characters; %s)",
            len(audio) / SAMPLE_RATE, time.monotonic() - started, len(text), self._quality_text(),
        )
        self._keep_turn(audio, text)
        return text

    def _quality_text(self) -> str:
        """How sure the model was: the lowest average log-probability and the highest no-speech chance among the
        segments kept. Near 0 / 0 is clear speech; below about -1 or above about 0.5 is shaky. Only logged."""
        if not self._quality:
            return "no segments"
        worst = min(logprob for logprob, _ in self._quality)
        silent = max(no_speech for _, no_speech in self._quality)
        return "confidence: lowest logprob %.2f, highest no-speech %.2f" % (worst, silent)

    def _keep_turn(self, audio: np.ndarray, text: str) -> None:
        """STT_SAVE_AUDIO: write the audio the model heard (after masking) and what it made of it."""
        folder = self._config.save_audio_dir
        if folder is None:
            return
        try:
            folder.mkdir(parents=True, exist_ok=True)
            stem = time.strftime("%Y%m%d_%H%M%S") + "_%03d" % (int(time.time() * 1000) % 1000)
            pcm = (np.clip(audio, -1.0, 1.0) * 32767).astype(np.int16)
            with wave.open(str(folder / f"{stem}.wav"), "wb") as out:
                out.setnchannels(1)
                out.setsampwidth(2)
                out.setframerate(SAMPLE_RATE)
                out.writeframes(pcm.tobytes())
            (folder / f"{stem}.txt").write_text(f"{text}\n{self._quality_text()}\n", encoding="utf-8")
        except Exception:  # noqa: BLE001 - keeping recordings must never stop a conversation
            logger.warning("Could not keep the turn audio", exc_info=True)

    def _transcribe_with_thai_model(self, model, audio: np.ndarray, chosen: str | None) -> str:
        """Thai to the Thai model; English (decided by the detector) to the general one.

        The Thai model must never get English: it translates it into Thai
        ("Where is the library?" became "โรงพยาบาลอยู่ไหน").
        """
        language = chosen if chosen == "th" else self._thai_or_english(audio)
        if self._unwanted():
            return ""
        if language == UNSURE:
            return self._transcribe_both_ways(model, audio)
        if language == "en":
            return self._collect(self._start(model, audio, "en"))
        # On the processor the Thai model takes about 5 s whatever it is asked and a wider beam costs a
        # second more for almost no gain, so it decodes greedily there; on the graphics card it does not.
        beam = GPU_THAI_BEAM if self._device == "cuda" else CPU_THAI_BEAM
        thai = self._start(self._thai, audio, "th", beam_size=beam, prompt=self._config.thai_prompt)
        return self._without_prompt(join_thai_words(self._collect(thai)))

    def _without_prompt(self, text: str) -> str:
        """A transcript that is only a piece of the prompt is the model repeating it (it does that on noise): nothing was said."""
        prompt = self._config.thai_prompt
        if prompt and len(text) >= 4 and join_thai_words(text) in join_thai_words(prompt):
            logger.info("Dropped a transcript that only repeats the speech prompt: %r", text)
            return ""
        return text

    def _thai_or_english(self, audio: np.ndarray) -> str:
        """English only when the detector is fairly sure of it; otherwise Thai.

        Measured with the tiny detector: English speech scored 0.84-0.98 for English,
        room noise 0.17-0.20 for English and 0.01 for Thai. Choosing the larger of the
        two sent unclear sound to the English model, which wrote sentences nobody said
        ("We got the money.", "Ha ha ha ha!").
        """
        detector = self._detector or self._model
        _language, _probability, scores = detector.detect_language(audio)
        scores = dict(scores)
        thai, english = scores.get("th", 0.0), scores.get("en", 0.0)
        language = classify_language(thai, english)
        if language == UNSURE and self._detector is not None and self._model is not None:
            # A second opinion from the bigger model, and the two are averaged.
            _language, _probability, second = self._model.detect_language(audio)
            second = dict(second)
            thai, english = (thai + second.get("th", 0.0)) / 2, (english + second.get("en", 0.0)) / 2
            language = classify_language(thai, english)
        logger.info("Language check: th=%.2f en=%.2f -> %s", thai, english, language)
        return language

    def _transcribe_both_ways(self, model, audio: np.ndarray) -> str:
        """The language is not clear: transcribe as English (the general model) and as Thai (the Thai model) and keep the
        one with the better evidence. This costs a second transcription, and only for speech that is hard to tell."""
        english, english_score = self._collect_scored(self._start(model, audio, "en"))
        if self._unwanted():
            return ""
        beam = GPU_THAI_BEAM if self._device == "cuda" else CPU_THAI_BEAM
        thai, thai_score = self._collect_scored(
            self._start(self._thai, audio, "th", beam_size=beam, prompt=self._config.thai_prompt))
        thai = self._without_prompt(join_thai_words(thai))
        choice = choose_transcript(english, english_score, thai, thai_score)
        logger.info(
            "Language unclear: English %r (%.2f) against Thai %r (%.2f) -> %s",
            english[:60], english_score, thai[:60], thai_score, choice,
        )
        return english if choice == "en" else thai

    def _transcribe_thai_or_english(self, model, audio: np.ndarray) -> str:
        # One pass: "multilingual" detects the language from the same encoding
        # that is decoded (language=None would encode the audio twice).
        text = self._collect(self._start(model, audio, SPOKEN_LANGUAGES[0], multilingual=True))
        if not text or is_thai_or_english(text):
            return text
        # Neither (a short Thai question can come out in Vietnamese or Lao):
        # Thai, the language of most visitors. Only this case costs a second pass.
        logger.info("Heard neither Thai nor English (%r); transcribing again as Thai", text)
        return self._collect(self._start(model, audio, SPOKEN_LANGUAGES[0]))

    @staticmethod
    def _start(model, audio: np.ndarray, language: str | None, multilingual: bool = False, beam_size: int = 5,
               prompt: str = ""):
        """The words are decoded later, while ``_collect`` iterates the segments."""
        extra = {"initial_prompt": prompt} if prompt else {}
        return model.transcribe(
            audio,
            language=language,
            task="transcribe",
            beam_size=beam_size,
            vad_filter=True,
            condition_on_previous_text=False,
            without_timestamps=True,
            multilingual=multilingual,
            **extra,
        )[0]

    def _unwanted(self) -> bool:
        cancelled = self._cancelled
        return cancelled is not None and cancelled()

    def _collect(self, segments) -> str:
        return self._collect_scored(segments)[0]

    def _collect_scored(self, segments) -> tuple[str, float]:
        """The text, and how sure the model was of it (the mean of the segments' average log-probabilities)."""
        # ``segments`` is a generator; decoding happens while iterating.
        kept, scores = [], []
        for segment in segments:
            if self._unwanted():
                return "", NO_SCORE
            if not (segment.no_speech_prob > NO_SPEECH_PROB and segment.avg_logprob < LOW_LOGPROB):
                kept.append(segment.text)
                scores.append(segment.avg_logprob)
                self._quality.append((segment.avg_logprob, segment.no_speech_prob))
        text = clean_transcript("".join(kept))
        return text, (sum(scores) / len(scores) if scores and text else NO_SCORE)

    def _bilingual(self, override: str | None) -> bool:
        """No override, and the configured language is one of the two spoken here."""
        return override is None and self._config.language.strip().lower() in SPOKEN_LANGUAGES

    def _language(self, override: str | None = None) -> str | None:
        """``None`` lets the model detect the language of each utterance."""
        language = (override or self._config.language).strip().lower()
        return None if language in ("", "auto") else language

    def _load_locked(self):
        if self._model is not None:
            return self._model
        config = self._config
        # Without symlink rights (the normal case on Windows) huggingface_hub
        # stores plain copies and prints a long warning about it on every load.
        os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
        try:
            from faster_whisper import WhisperModel  # noqa: F401 - fails early, with a clear message
        except ImportError as exc:
            raise SpeechToTextError("ยังไม่ได้ติดตั้ง faster-whisper") from exc

        device, compute_type = self._choose_device()
        try:
            self._load_models(device, compute_type)
        except Exception as exc:
            if device != "cuda":
                logger.exception("Could not load speech model %s", config.model)
                raise SpeechToTextError(
                    "โหลดโมเดลถอดเสียงไม่สำเร็จ (ครั้งแรกต้องเชื่อมต่ออินเทอร์เน็ตเพื่อดาวน์โหลดโมเดล)"
                ) from exc
            # All three models move to the processor together, so the Thai model is never
            # replaced by the weaker general one just because the card was out of memory.
            logger.warning("Speech models could not be loaded on the graphics card (%r); using the processor", exc)
            self._model = self._thai = self._detector = None
            try:
                self._load_models("cpu", CPU_COMPUTE_TYPE)
            except Exception as cpu_exc:
                logger.exception("Could not load speech model %s", config.model)
                raise SpeechToTextError(
                    "โหลดโมเดลถอดเสียงไม่สำเร็จ (ครั้งแรกต้องเชื่อมต่ออินเทอร์เน็ตเพื่อดาวน์โหลดโมเดล)"
                ) from cpu_exc
        return self._model

    def _choose_device(self) -> tuple[str, str]:
        """The graphics card when it is asked for (or "auto") and can be used, else the processor."""
        config = self._config
        wanted = config.device.strip().lower()
        if wanted in ("cuda", "auto"):
            register_cuda_libraries()
            if cuda_available():
                return "cuda", config.compute_type if config.compute_type != CPU_COMPUTE_TYPE else "float16"
            logger.info("No usable graphics card for speech (STT_DEVICE=%s); using the processor", wanted)
        compute = config.compute_type
        if compute.startswith("float16") or compute.startswith("bfloat16"):
            compute = CPU_COMPUTE_TYPE  # half precision does not exist on the processor
        return "cpu", compute

    def _load_models(self, device: str, compute_type: str) -> None:
        from faster_whisper import WhisperModel

        config = self._config
        download_root = None
        if config.model_dir is not None:
            config.model_dir.mkdir(parents=True, exist_ok=True)
            download_root = str(config.model_dir)
        logger.info("Loading speech model %s (device=%s, compute_type=%s)", config.model, device, compute_type)
        started = time.monotonic()
        options = {"device": device, "compute_type": compute_type}
        self._model = _open(WhisperModel, config.model, options, download_root)
        self._device, self._compute_type = device, compute_type
        logger.info("Speech model loaded in %.1f s", time.monotonic() - started)
        if config.thai_model:
            started = time.monotonic()
            try:
                self._thai = _open(WhisperModel, config.thai_model, options, download_root, config.thai_model_revision)
                logger.info("Thai speech model %s loaded in %.1f s", config.thai_model, time.monotonic() - started)
            except Exception:
                if device == "cuda":
                    raise  # fall back to the processor with the Thai model, not to the general one
                logger.exception("Could not load the Thai speech model %s; Thai uses %s",
                                 config.thai_model, config.model)
        if self._thai is not None and config.detector_model and config.detector_model != config.model:
            try:
                self._detector = _open(WhisperModel, config.detector_model, options, download_root)
            except Exception:
                if device == "cuda":
                    raise
                # Slower (about 1.5 s more per question), but still right.
                logger.exception("Could not load %s; the language is checked with %s",
                                 config.detector_model, config.model)

    def _warm_up_locked(self) -> None:
        """Run each model once on sound that reaches its encoder, so the first real question is not
        the slow one (the first call on the graphics card takes about twice as long). Silence would
        not do: the voice filter removes it and the encoder never runs."""
        if self._device != "cuda" or self._model is None:
            return
        started = time.monotonic()
        rng = np.random.default_rng(0)
        noise = (rng.standard_normal(SAMPLE_RATE * 2) * 0.05).astype(np.float32)
        try:
            for model, language in ((self._thai, "th"), (self._model, "en")):
                if model is not None:
                    segments = model.transcribe(
                        noise, language=language, beam_size=1, vad_filter=False,
                        without_timestamps=True, condition_on_previous_text=False,
                    )[0]
                    for _ in segments:
                        pass
            if self._detector is not None:
                self._detector.detect_language(noise)
        except Exception:  # noqa: BLE001 - a warm-up must never stop the program
            logger.warning("Speech warm-up failed", exc_info=True)
            return
        logger.info("Speech models warmed up in %.1f s", time.monotonic() - started)


def _open(whisper_model, name: str, options: dict, download_root: str | None, revision: str | None = None):
    """The copy already on disk if there is one (no network, so the kiosk starts the same way with or
    without internet), otherwise download it.

    Only "not on disk" leads to a download. Any other failure (no graphics card, out of memory) is
    raised as it is, so it is never mistaken for a missing model and answered with a network fetch.
    """
    try:
        from faster_whisper.utils import download_model
    except ImportError:  # a stand-in package (tests) or an old version: let the model class look for it itself
        extra = {"download_root": download_root, "revision": revision}
        try:
            return whisper_model(name, local_files_only=True, **options, **extra)
        except Exception:
            logger.info("Speech model %s is not on disk yet; downloading", name)
            return whisper_model(name, **options, **extra)

    try:
        path = download_model(name, local_files_only=True, cache_dir=download_root, revision=revision)
    except Exception:
        logger.info("Speech model %s is not on disk yet; downloading", name)
        path = download_model(name, local_files_only=False, cache_dir=download_root, revision=revision)
    return whisper_model(path, **options)


_THAI_GAP = re.compile(r"(?<=[฀-๿]) (?=[฀-๿])")


def join_thai_words(text: str) -> str:
    """The Thai model sometimes spaces out words ("ค่า เทอม เท่าไหร่"); Thai is written without them."""
    return _THAI_GAP.sub("", text)


def normalize_level(audio: np.ndarray) -> np.ndarray:
    """Raise quiet audio towards ``TARGET_PEAK`` (at most ``MAX_GAIN`` times); never lower it."""
    peak = float(np.max(np.abs(audio))) if len(audio) else 0.0
    if peak <= 0.0 or peak >= TARGET_PEAK:
        return audio
    return audio * min(TARGET_PEAK / peak, MAX_GAIN)


def classify_language(thai: float, english: float) -> str:
    """"th", "en" or UNSURE from the detector's probabilities for Thai and English (see the constants above)."""
    if english >= MIN_ENGLISH_PROB and english > thai:
        return "en"
    if thai >= MIN_THAI_PROB and thai > english:
        return "th"
    if english >= UNSURE_MIN_ENGLISH and english + thai >= UNSURE_MIN_EVIDENCE:
        return UNSURE
    return "th"  # noise, or something else (a Thai question heard as Vietnamese): Thai, as it always was


def _thai_letters(text: str) -> int:
    return sum("\u0e01" <= char <= "\u0e5b" for char in text)


def _latin_letters(text: str) -> int:
    return sum(char.isascii() and char.isalpha() for char in text)


def choose_transcript(english: str, english_score: float, thai: str, thai_score: float) -> str:
    """"en" or "th": which transcript of the same speech to believe.

    Each must look like its language (English: some English words and no Thai script; Thai: mostly Thai letters). If only one
    does, it wins. If both do, the English one needs a clearly better mean log-probability, because most visitors speak Thai
    and the Thai model, given English, writes it out in Thai with little confidence but without failing.
    """
    english_words = [word for word in english.split() if sum(char.isalpha() for char in word) >= 2]
    english_ok = bool(english) and _thai_letters(english) == 0 and len(english_words) >= MIN_ENGLISH_WORDS
    thai_all = _thai_letters(thai) + _latin_letters(thai)
    thai_ok = bool(thai) and thai_all > 0 and _thai_letters(thai) / thai_all >= 0.5
    if english_ok and not thai_ok:
        return "en"
    if thai_ok and not english_ok:
        return "th"
    if english_ok and thai_ok:
        return "en" if english_score > thai_score + ENGLISH_MARGIN else "th"
    return "th"


def is_thai_or_english(text: str) -> bool:
    """Thai script anywhere, or nothing but plain ASCII (English)."""
    return any("฀" <= char <= "๿" for char in text) or text.isascii()


# Words the Thai model writes for words of this event, each a non-word or a wrong word in this place ("สิ่งผลิต" is not Thai).
_MISHEARD = (
    (re.compile(r"สิ่ง\s*ผลิต"), "สิ่งประดิษฐ์"),  # 11 Nov 2026: "แนะนำสิ่งประดิษฐ์หน่อย" heard as "แนะนำสิ่งผลิตหน่อย"
    (re.compile(r"สิ่ง\s*ประดิ[ดตษ](?!ฐ)์?"), "สิ่งประดิษฐ์"),
    (re.compile(r"สิ่ง\s*ประ\s*ดิษ\s*ฐ์"), "สิ่งประดิษฐ์"),
    (re.compile(r"ไอ\s*(?:รา|ล่า|ลา|ร้า)(?!วัต|พต)"), "ไอร่า"),  # the robot's name
)


def fix_known_mishearings(text: str) -> str:
    for pattern, right in _MISHEARD:
        text = pattern.sub(right, text)
    return text


def clean_transcript(text: str) -> str:
    """Drop Whisper's inventions: subtitle credits and words repeated in a loop."""
    text = fix_known_mishearings(" ".join(text.split()))
    if any(phrase in text.lower() for phrase in _HALLUCINATIONS):
        logger.info("Dropped a transcript Whisper is known to invent: %r", text)
        return ""
    # "ครับ ครับ ครับ ครับ ..." — keep at most three of the same word in a row.
    words, kept = text.split(" "), []
    for word in words:
        if len(kept) >= 3 and kept[-1] == kept[-2] == kept[-3] == word:
            continue
        kept.append(word)
    return " ".join(kept).strip()
