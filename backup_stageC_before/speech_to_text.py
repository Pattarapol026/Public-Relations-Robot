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
from dataclasses import dataclass
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
            "Transcribed %.1f s of audio in %.1f s (%d characters)",
            len(audio) / SAMPLE_RATE, time.monotonic() - started, len(text),
        )
        return text

    def _transcribe_with_thai_model(self, model, audio: np.ndarray, chosen: str | None) -> str:
        """Thai to the Thai model; English (decided by the detector) to the general one.

        The Thai model must never get English: it translates it into Thai
        ("Where is the library?" became "โรงพยาบาลอยู่ไหน").
        """
        language = chosen if chosen == "th" else self._thai_or_english(audio)
        if self._unwanted():
            return ""
        if language == "en":
            return self._collect(self._start(model, audio, "en"))
        # On the processor the Thai model takes about 5 s whatever it is asked and a wider beam costs a
        # second more for almost no gain, so it decodes greedily there; on the graphics card it does not.
        beam = GPU_THAI_BEAM if self._device == "cuda" else CPU_THAI_BEAM
        return join_thai_words(self._collect(self._start(self._thai, audio, "th", beam_size=beam)))

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
        language = "en" if english >= MIN_ENGLISH_PROB and english > thai else "th"
        logger.info("Language check: th=%.2f en=%.2f -> %s", thai, english, language)
        return language

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
    def _start(model, audio: np.ndarray, language: str | None, multilingual: bool = False, beam_size: int = 5):
        """The words are decoded later, while ``_collect`` iterates the segments."""
        return model.transcribe(
            audio,
            language=language,
            task="transcribe",
            beam_size=beam_size,
            vad_filter=True,
            condition_on_previous_text=False,
            without_timestamps=True,
            multilingual=multilingual,
        )[0]

    def _unwanted(self) -> bool:
        cancelled = self._cancelled
        return cancelled is not None and cancelled()

    def _collect(self, segments) -> str:
        # ``segments`` is a generator; decoding happens while iterating.
        kept = []
        for segment in segments:
            if self._unwanted():
                return ""
            if not (segment.no_speech_prob > NO_SPEECH_PROB and segment.avg_logprob < LOW_LOGPROB):
                kept.append(segment.text)
        return clean_transcript("".join(kept))

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


def is_thai_or_english(text: str) -> bool:
    """Thai script anywhere, or nothing but plain ASCII (English)."""
    return any("฀" <= char <= "๿" for char in text) or text.isascii()


def clean_transcript(text: str) -> str:
    """Drop Whisper's inventions: subtitle credits and words repeated in a loop."""
    text = " ".join(text.split())
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
