"""End-of-speech detection, resampling and the speech-to-text wrapper (no real model)."""
from __future__ import annotations

import sys
import threading
import time
import types

import numpy as np
import pytest

from audio.endpointing import EndpointConfig, EndpointDecision, Endpointer
from audio import microphone as microphone_module
from audio.microphone import (
    PRIVACY_BLOCKED_MESSAGE,
    TARGET_SAMPLE_RATE,
    MicrophoneError,
    MicrophoneService,
    resample,
)
from audio.speech_to_text import (
    SpeechToTextError,
    SpeechToTextService,
    SttConfig,
    clean_transcript,
    join_thai_words,
    normalize_level,
)
from config.settings import THAI_SPEECH_MODEL, THAI_SPEECH_MODEL_REVISION, load_settings

CONFIG = EndpointConfig(
    level_threshold=0.02, min_speech_s=0.25, silence_s=1.0, no_speech_timeout_s=5.0, max_seconds=20.0
)
LOUD, QUIET, STEP = 0.2, 0.001, 0.05


def feed(endpointer: Endpointer, level: float, seconds: float) -> EndpointDecision:
    decision = EndpointDecision.CONTINUE
    for _ in range(round(seconds / STEP)):
        decision = endpointer.update(level, STEP)
        if decision is not EndpointDecision.CONTINUE:
            break
    return decision


# --- end-of-speech detection ---


def test_speech_followed_by_silence_finishes():
    endpointer = Endpointer(CONFIG)
    assert feed(endpointer, QUIET, 1.0) is EndpointDecision.CONTINUE
    assert feed(endpointer, LOUD, 1.5) is EndpointDecision.CONTINUE
    assert feed(endpointer, QUIET, 0.9) is EndpointDecision.CONTINUE  # a pause, not the end
    assert feed(endpointer, LOUD, 0.5) is EndpointDecision.CONTINUE
    assert feed(endpointer, QUIET, 1.1) is EndpointDecision.FINISHED
    assert endpointer.heard_speech


def test_silence_only_times_out_as_no_speech():
    assert feed(Endpointer(CONFIG), QUIET, 6.0) is EndpointDecision.NO_SPEECH


def test_short_noise_bursts_are_not_speech():
    endpointer = Endpointer(CONFIG)
    for _ in range(8):  # door slams: 0.1 s loud, 0.4 s quiet
        feed(endpointer, LOUD, 0.1)
        decision = feed(endpointer, QUIET, 0.4)
    assert not endpointer.heard_speech
    assert feed(endpointer, QUIET, 2.0) is EndpointDecision.NO_SPEECH
    assert decision is EndpointDecision.CONTINUE


def test_nonstop_speech_is_cut_at_the_limit():
    assert feed(Endpointer(CONFIG), LOUD, 25.0) is EndpointDecision.TOO_LONG


def test_quiet_microphone_still_hears_speech_above_the_room_noise():
    # A laptop microphone without gain: room noise 0.002, speech 0.012, both under 0.02.
    endpointer = Endpointer(CONFIG)
    assert feed(endpointer, 0.002, 1.0) is EndpointDecision.CONTINUE
    assert feed(endpointer, 0.012, 1.0) is EndpointDecision.CONTINUE
    assert endpointer.heard_speech
    assert feed(endpointer, 0.002, 1.3) is EndpointDecision.FINISHED


def test_noise_level_never_raises_the_configured_threshold():
    endpointer = Endpointer(CONFIG)
    feed(endpointer, 0.015, 1.0)  # a noisy room, just under the threshold
    assert endpointer.threshold == CONFIG.level_threshold
    assert not endpointer.heard_speech


def test_reset_starts_a_new_utterance():
    endpointer = Endpointer(CONFIG)
    feed(endpointer, LOUD, 1.0)
    endpointer.reset()
    assert not endpointer.heard_speech
    assert feed(endpointer, QUIET, 1.5) is EndpointDecision.CONTINUE  # no "finished" from old speech


# --- resampling ---


def test_resample_changes_length_and_keeps_the_signal():
    seconds, source_rate = 0.5, 48_000
    t = np.arange(int(seconds * source_rate)) / source_rate
    tone = np.sin(2 * np.pi * 440 * t).astype(np.float32)
    result = resample(tone, source_rate, TARGET_SAMPLE_RATE)
    assert result.dtype == np.float32
    assert len(result) == int(seconds * TARGET_SAMPLE_RATE)
    expected = np.sin(2 * np.pi * 440 * np.arange(len(result)) / TARGET_SAMPLE_RATE)
    assert np.max(np.abs(result - expected)) < 0.05


def test_resample_same_rate_and_empty_input():
    samples = np.ones(100, dtype=np.float32)
    assert resample(samples, 16_000, 16_000) is samples
    assert len(resample(np.zeros(0, dtype=np.float32), 44_100, 16_000)) == 0


# --- microphone selection (scripted PortAudio) ---


class FakeStream:
    def __init__(self, sd, device, samplerate, blocksize, channels, callback, **_options) -> None:
        behaviour = sd.behaviour.get(device, "works")
        if behaviour == "refuses" or (behaviour == "48k only" and samplerate != 48_000):
            raise RuntimeError("Unanticipated host error [MME error 1]")
        self._sd, self._device, self._callback = sd, device, callback
        self._channels, self._frames = channels, blocksize
        self._running = threading.Event()
        self._thread = None

    def start(self) -> None:
        sd = self._sd
        sd.opened.append(self._device)
        if sd.behaviour.get(self._device) == "no blocks":
            return
        level = 0.0 if sd.behaviour.get(self._device) == "silent" else 0.01
        self._running.set()

        def run():
            status = types.SimpleNamespace(input_overflow=False)
            while self._running.is_set():
                block = np.full((self._frames, self._channels), level, dtype=np.float32)
                self._callback(block, self._frames, None, status)
                time.sleep(0.01)

        self._thread = threading.Thread(target=run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running.clear()
        if self._thread is not None:
            self._thread.join()

    def close(self) -> None:
        pass


class FakeSoundDevice:
    """This laptop: Windows privacy blocks MME and WASAPI; WDM-KS reaches the built-in array."""

    def __init__(self) -> None:
        self.hostapis = [
            {"name": "MME", "default_input_device": 0},
            {"name": "Windows WASAPI", "default_input_device": 2},
            {"name": "Windows WDM-KS", "default_input_device": 3},  # the empty jack
        ]
        self.devices = [
            {"name": "Microphone Array (Intel", "hostapi": 0, "max_input_channels": 4, "default_samplerate": 44_100.0},
            {"name": "Stereo Mix", "hostapi": 0, "max_input_channels": 2, "default_samplerate": 44_100.0},
            {"name": "Microphone Array (Intel SST)", "hostapi": 1, "max_input_channels": 2, "default_samplerate": 48_000.0},
            {"name": "Microphone (Realtek)", "hostapi": 2, "max_input_channels": 2, "default_samplerate": 44_100.0},
            {"name": "Microphone Array 1 ()", "hostapi": 2, "max_input_channels": 2, "default_samplerate": 48_000.0},
            {"name": "Speakers", "hostapi": 0, "max_input_channels": 0, "default_samplerate": 48_000.0},
        ]
        self.behaviour = {0: "refuses", 2: "refuses", 3: "silent", 4: "48k only"}
        self.default = types.SimpleNamespace(device=[0, 5], hostapi=0)
        self.opened: list[int] = []

    def query_devices(self, device=None, kind=None):
        return self.devices if device is None else self.devices[device]

    def query_hostapis(self, index=None):
        return self.hostapis if index is None else self.hostapis[index]

    def InputStream(self, **options):  # noqa: N802 - the sounddevice name
        return FakeStream(self, **options)


@pytest.fixture
def fake_sd(monkeypatch):
    sd = FakeSoundDevice()
    monkeypatch.setattr(microphone_module, "_sounddevice", lambda: sd)
    monkeypatch.setattr(microphone_module, "windows_microphone_blocked", lambda: True)
    return sd


def test_built_in_microphone_is_found_when_windows_blocks_the_usual_apis(fake_sd):
    microphone = MicrophoneService()
    microphone.start()
    try:
        assert microphone.device_label == "Microphone Array 1 () [WDM-KS]"
        assert microphone.peak > 0.0
    finally:
        microphone.cancel()
    # Tried in order; the empty jack (only zeros) was passed over for the array.
    assert fake_sd.opened == [3, 4]


def test_the_working_microphone_is_remembered(fake_sd):
    microphone = MicrophoneService()
    microphone.start()
    microphone.cancel()
    fake_sd.opened.clear()
    microphone.start()
    microphone.cancel()
    assert fake_sd.opened == [4]  # straight to it, no second search
    assert [d.name for d in microphone.list_input_devices() if d.is_default] == [
        "Microphone Array 1 () [WDM-KS]"
    ]


def test_device_list_has_each_api_and_no_loopback(fake_sd):
    names = [d.name for d in MicrophoneService().list_input_devices()]
    assert "Stereo Mix [MME]" not in names and all("Speakers" not in name for name in names)
    assert names[0] == "Microphone Array (Intel [MME]"  # the system default first
    assert "Microphone Array 1 () [WDM-KS]" in names


def test_chosen_microphone_is_used_and_a_missing_one_falls_back(fake_sd):
    microphone = MicrophoneService()
    microphone.start("Microphone (Realtek) [WDM-KS]", fallback=False)
    assert microphone.device_label == "Microphone (Realtek) [WDM-KS]"  # chosen, even if silent
    microphone.cancel()

    with pytest.raises(MicrophoneError):
        microphone.start("USB Mic", fallback=False)  # the Admin's test: exactly what was chosen
    microphone.start("USB Mic")  # a visitor: any microphone that works
    assert microphone.device_label == "Microphone Array 1 () [WDM-KS]"
    microphone.cancel()


def test_privacy_block_is_explained_when_nothing_opens(fake_sd):
    fake_sd.behaviour = {index: "refuses" for index in range(5)}
    with pytest.raises(MicrophoneError) as error:
        MicrophoneService().probe()
    assert str(error.value) == PRIVACY_BLOCKED_MESSAGE


def test_stream_that_delivers_nothing_is_not_counted_as_open(fake_sd, monkeypatch):
    monkeypatch.setattr(microphone_module, "FIRST_BLOCK_TIMEOUT_S", 0.1)
    fake_sd.behaviour[4] = "no blocks"
    with pytest.raises(MicrophoneError):
        MicrophoneService().start("Microphone Array 1 () [WDM-KS]", fallback=False)


def test_probe_closes_the_microphone_again(fake_sd):
    microphone = MicrophoneService()
    assert microphone.probe() == "Microphone Array 1 () [WDM-KS]"
    assert not microphone.is_recording


# --- speech-to-text wrapper ---


class FakeSegment:
    def __init__(self, text: str, no_speech_prob: float = 0.05, avg_logprob: float = -0.3) -> None:
        self.text = text
        self.no_speech_prob = no_speech_prob
        self.avg_logprob = avg_logprob


class FakeWhisperModel:
    instances: list["FakeWhisperModel"] = []
    fail_on_load = False
    fail_on_transcribe = False
    # Segments returned by each call in turn; the last one repeats.
    results: list[list[FakeSegment]] = []
    by_model: dict[str, list[list[FakeSegment]]] = {}  # or the results of each model by its name

    attempts: list[bool] = []  # local_files_only of every construction attempt
    cached = True
    missing: set[str] = set()  # model names that cannot be loaded at all
    heard = "th"  # what detect_language finds
    scores: list[tuple[str, float]] = []  # or exactly these scores

    def __init__(
        self, model, device=None, compute_type=None, download_root=None, local_files_only=False, revision=None
    ) -> None:
        FakeWhisperModel.attempts.append(local_files_only)
        if local_files_only and not FakeWhisperModel.cached:
            raise OSError("model not found in local cache")
        if FakeWhisperModel.fail_on_load or model in FakeWhisperModel.missing:
            raise OSError("no network")
        self.init_args = (model, device, compute_type, download_root)
        self.name, self.revision = model, revision
        self.calls: list[dict] = []
        self.detections = 0
        FakeWhisperModel.instances.append(self)

    def detect_language(self, audio):
        self.detections += 1
        if FakeWhisperModel.scores:
            top = max(FakeWhisperModel.scores, key=lambda pair: pair[1])
            return top[0], top[1], FakeWhisperModel.scores
        other = "en" if FakeWhisperModel.heard == "th" else "th"
        return FakeWhisperModel.heard, 0.9, [(FakeWhisperModel.heard, 0.9), ("lo", 0.06), (other, 0.04)]

    def transcribe(self, audio, **options):
        if FakeWhisperModel.fail_on_transcribe:
            raise RuntimeError("decoder crashed")
        self.calls.append({"audio": audio, **options})
        results = (FakeWhisperModel.by_model.get(self.name) or FakeWhisperModel.results
                   or [[FakeSegment(" สวัสดีครับ"), FakeSegment(" hello")]])
        return iter(results[min(len(self.calls), len(results)) - 1]), object()


@pytest.fixture
def fake_whisper(monkeypatch):
    FakeWhisperModel.instances = []
    FakeWhisperModel.attempts = []
    FakeWhisperModel.cached = True
    FakeWhisperModel.fail_on_load = False
    FakeWhisperModel.fail_on_transcribe = False
    FakeWhisperModel.results = []
    FakeWhisperModel.by_model = {}
    FakeWhisperModel.missing = set()
    FakeWhisperModel.heard = "th"
    FakeWhisperModel.scores = []
    module = types.ModuleType("faster_whisper")
    module.WhisperModel = FakeWhisperModel
    monkeypatch.setitem(sys.modules, "faster_whisper", module)
    return FakeWhisperModel


def one_second() -> np.ndarray:
    return np.zeros(TARGET_SAMPLE_RATE, dtype=np.float32)


def test_model_is_loaded_once_and_reused(fake_whisper, tmp_path):
    service = SpeechToTextService(
        SttConfig(model="small", device="cpu", compute_type="int8", language="th",
                  model_dir=tmp_path / "models")
    )
    assert not service.is_loaded
    service.load()
    service.load()
    assert service.transcribe(one_second()) == "สวัสดีครับ hello"
    assert service.transcribe(one_second()) == "สวัสดีครับ hello"

    assert len(fake_whisper.instances) == 1
    model = fake_whisper.instances[0]
    assert model.init_args == ("small", "cpu", "int8", str(tmp_path / "models"))
    assert len(model.calls) == 2
    call = model.calls[0]
    assert (call["language"], call["task"], call["vad_filter"]) == ("th", "transcribe", True)
    assert call["multilingual"]  # Thai configured: Thai or English, detected in the same pass
    assert call["audio"].dtype == np.float32


def test_thai_setting_accepts_english_in_one_pass(fake_whisper):
    fake_whisper.results = [[FakeSegment(" Where is the toilet?")]]
    service = SpeechToTextService(SttConfig(language="th"))
    assert service.transcribe(one_second()) == "Where is the toilet?"
    assert len(fake_whisper.instances[0].calls) == 1


def test_speech_heard_as_neither_thai_nor_english_is_transcribed_again_as_thai(fake_whisper):
    # A short Thai question detected as Vietnamese (seen with the laptop microphone).
    fake_whisper.results = [[FakeSegment(" và mặc điểm đấy thế này.")], [FakeSegment(" สมัครเรียนได้ที่ไหน")]]
    service = SpeechToTextService(SttConfig(language="th"))
    assert service.transcribe(one_second()) == "สมัครเรียนได้ที่ไหน"
    second = fake_whisper.instances[0].calls[1]
    assert (second["language"], second["multilingual"]) == ("th", False)


def test_english_mode_forces_english(fake_whisper):
    service = SpeechToTextService(SttConfig(language="th"))
    service.transcribe(one_second(), "en")
    call = fake_whisper.instances[0].calls[0]
    assert (call["language"], call["multilingual"]) == ("en", False)


def test_known_whisper_inventions_and_non_speech_are_dropped(fake_whisper):
    service = SpeechToTextService(SttConfig(language="th"))
    fake_whisper.results = [[FakeSegment(" ขอบคุณที่รับชม")]]
    assert service.transcribe(one_second()) == ""
    fake_whisper.instances[0].calls.clear()
    fake_whisper.results = [[FakeSegment(" ห้องน้ำ"), FakeSegment(" อยู่ไหน", 0.9, -1.5)]]
    assert service.transcribe(one_second()) == "ห้องน้ำ"  # the model's own "not speech" segment
    assert clean_transcript("ครับ ครับ ครับ ครับ ครับ ครับ") == "ครับ ครับ ครับ"


def test_quiet_audio_is_raised_but_never_lowered():
    quiet = np.array([0.0, 0.01, -0.02], dtype=np.float32)
    assert np.isclose(np.abs(normalize_level(quiet)).max(), 0.4)  # 20x at most
    assert np.isclose(np.abs(normalize_level(quiet * 5)).max(), 0.5)
    loud = np.array([0.0, 0.8], dtype=np.float32)
    assert normalize_level(loud) is loud
    assert not normalize_level(np.zeros(3, dtype=np.float32)).any()


def test_cached_model_loads_without_network_and_missing_model_is_downloaded(fake_whisper):
    SpeechToTextService(SttConfig()).load()
    assert fake_whisper.attempts == [True]  # found on disk: no download attempt

    fake_whisper.attempts = []
    fake_whisper.cached = False
    service = SpeechToTextService(SttConfig())
    service.load()
    assert fake_whisper.attempts == [True, False]  # not on disk: second attempt may download
    assert service.is_loaded


def test_language_auto_lets_the_model_detect(fake_whisper):
    service = SpeechToTextService(SttConfig(language="auto"))
    service.transcribe(one_second())
    assert fake_whisper.instances[0].calls[0]["language"] is None


def test_too_short_audio_returns_empty_without_loading_the_model(fake_whisper):
    service = SpeechToTextService(SttConfig())
    assert service.transcribe(np.zeros(1000, dtype=np.float32)) == ""
    assert fake_whisper.instances == []


def test_stereo_audio_is_rejected(fake_whisper):
    with pytest.raises(SpeechToTextError):
        SpeechToTextService(SttConfig()).transcribe(np.zeros((16_000, 2), dtype=np.float32))


# --- the Thai model ---

THAI_CONFIG = SttConfig(language="th", thai_model="thai-medium", thai_model_revision="abc123", detector_model="tiny")


def models(fake_whisper) -> dict[str, FakeWhisperModel]:
    return {model.name: model for model in fake_whisper.instances}


def test_thai_speech_goes_to_the_thai_model_with_its_words_joined(fake_whisper):
    fake_whisper.results = [[FakeSegment(" ค่า เทอม เท่าไหร่")]]
    service = SpeechToTextService(THAI_CONFIG)
    assert service.transcribe(one_second()) == "ค่าเทอมเท่าไหร่"
    loaded = models(fake_whisper)
    assert service.uses_thai_model and loaded["thai-medium"].revision == "abc123"
    assert loaded["tiny"].detections == 1 and loaded["small"].detections == 0  # the fast detector decides
    assert [c["language"] for c in loaded["thai-medium"].calls] == ["th"]
    assert loaded["small"].calls == []


def test_english_never_reaches_the_thai_model(fake_whisper):
    # The Thai model translates English into Thai ("Where is the library?" -> "โรงพยาบาลอยู่ไหน").
    fake_whisper.heard = "en"
    fake_whisper.results = [[FakeSegment(" Where is the library?")]]
    service = SpeechToTextService(THAI_CONFIG)
    assert service.transcribe(one_second()) == "Where is the library?"
    loaded = models(fake_whisper)
    assert loaded["thai-medium"].calls == []
    call = loaded["small"].calls[0]
    assert (call["language"], call["multilingual"]) == ("en", False)


@pytest.mark.parametrize("scores, model", [
    ([("nn", 0.39), ("en", 0.20), ("th", 0.01)], "thai-medium"),  # room noise, as measured with tiny
    ([("vi", 0.61), ("th", 0.36), ("en", 0.00)], "thai-medium"),  # a Thai question heard as Vietnamese
    ([("en", 0.84), ("th", 0.01)], "small"),  # "Who are you?"
])
def test_english_only_when_the_detector_is_sure_of_it(fake_whisper, scores, model):
    """Unclear sound sent to the English model came back as sentences nobody said."""
    fake_whisper.scores = scores
    service = SpeechToTextService(THAI_CONFIG)
    service.transcribe(one_second())
    loaded = models(fake_whisper)
    assert [name for name in ("small", "thai-medium") if loaded[name].calls] == [model]


def test_english_chosen_on_screen_skips_the_check(fake_whisper):
    service = SpeechToTextService(THAI_CONFIG)
    service.transcribe(one_second(), "en")
    loaded = models(fake_whisper)
    assert loaded["tiny"].detections == 0 and loaded["thai-medium"].calls == []
    assert loaded["small"].calls[0]["language"] == "en"


def test_without_the_thai_model_thai_is_heard_as_before(fake_whisper):
    fake_whisper.missing = {"thai-medium"}
    service = SpeechToTextService(THAI_CONFIG)
    assert service.transcribe(one_second()) == "สวัสดีครับ hello"  # the one-pass small model
    assert not service.uses_thai_model
    loaded = models(fake_whisper)
    assert "tiny" not in loaded  # nothing to decide between
    assert loaded["small"].calls[0]["multilingual"]


def test_without_the_detector_the_general_model_decides(fake_whisper):
    fake_whisper.missing = {"tiny"}
    service = SpeechToTextService(THAI_CONFIG)
    service.transcribe(one_second())
    loaded = models(fake_whisper)
    assert loaded["small"].detections == 1 and len(loaded["thai-medium"].calls) == 1


def test_thai_words_are_joined_but_english_ones_are_not():
    assert join_thai_words("ขอ เบอร์ โทร หน่อย") == "ขอเบอร์โทรหน่อย"
    assert join_thai_words("ค่าเทอม ICT เท่าไหร่ ok then") == "ค่าเทอม ICT เท่าไหร่ ok then"


def test_the_thai_model_is_on_by_default_pinned_and_can_be_switched_off():
    settings = load_settings(environ={})
    assert settings.stt_thai_model == THAI_SPEECH_MODEL
    assert settings.stt_thai_model_revision == THAI_SPEECH_MODEL_REVISION
    assert settings.stt_detector_model == "tiny"
    assert load_settings(environ={"STT_THAI_MODEL": "none"}).stt_thai_model == ""
    other = load_settings(environ={"STT_THAI_MODEL": "someone/other-thai-ct2"})
    assert other.stt_thai_model_revision is None  # the pin belongs to the default model


def test_load_and_transcribe_failures_become_speech_to_text_errors(fake_whisper):
    fake_whisper.fail_on_load = True
    service = SpeechToTextService(SttConfig())
    with pytest.raises(SpeechToTextError):
        service.load()
    assert not service.is_loaded

    fake_whisper.fail_on_load = False
    service.load()  # a later attempt (e.g. network is back) can succeed
    fake_whisper.fail_on_transcribe = True
    with pytest.raises(SpeechToTextError):
        service.transcribe(one_second())


def test_a_job_nobody_wants_any_more_is_skipped_without_using_the_model(fake_whisper):
    service = SpeechToTextService(SttConfig(language="th"))
    assert service.transcribe(one_second(), cancelled=lambda: True) == ""
    assert fake_whisper.instances == [] or all(not model.calls for model in fake_whisper.instances)  # never decoded

    wanted = iter([False, True])  # wanted when it is handed in, not by the time it gets the lock
    assert service.transcribe(one_second(), cancelled=lambda: next(wanted)) == ""
    assert all(not model.calls for model in fake_whisper.instances)

    assert service.transcribe(one_second(), cancelled=lambda: False) == "สวัสดีครับ hello"  # still wanted: as usual


def test_a_job_that_becomes_unwanted_between_its_steps_stops_there(fake_whisper):
    service = SpeechToTextService(SttConfig(language="th"))
    answers = iter([False, False, True, True, True, True])  # wanted until the detector has run
    assert service.transcribe(one_second(), cancelled=lambda: next(answers, True)) == ""



# --- speech that is hard to tell (9 Nov 2026): both transcripts are made and the better evidence wins ---


def test_the_classification_of_the_two_probabilities():
    from audio.speech_to_text import UNSURE, classify_language

    assert classify_language(0.01, 0.84) == "en" and classify_language(0.80, 0.05) == "th"
    assert classify_language(0.30, 0.45) == UNSURE  # English, but not sure enough: neither can be left out
    assert classify_language(0.01, 0.20) == "th"  # room noise, as measured
    assert classify_language(0.36, 0.00) == "th"  # a Thai question heard as Vietnamese


def test_english_with_an_accent_is_kept_as_english_when_its_transcript_is_clearly_better(fake_whisper):
    fake_whisper.scores = [("en", 0.45), ("th", 0.30)]
    fake_whisper.by_model = {
        "small": [[FakeSegment(" Where is the library", avg_logprob=-0.35)]],  # the English pass
        "thai-medium": [[FakeSegment(" โรงพยาบาลอยู่ไหน", avg_logprob=-0.95)]],  # the Thai model, translating it
    }
    service = SpeechToTextService(THAI_CONFIG)
    assert service.transcribe(one_second()) == "Where is the library"
    loaded = models(fake_whisper)
    assert loaded["small"].calls and loaded["thai-medium"].calls  # both were asked


def test_thai_with_an_english_word_stays_thai_when_the_english_pass_has_nothing_to_say(fake_whisper):
    fake_whisper.scores = [("en", 0.35), ("th", 0.30)]
    fake_whisper.by_model = {
        "small": [[FakeSegment(" IT", avg_logprob=-0.9)]],  # one English word is not an English sentence
        "thai-medium": [[FakeSegment(" สมัครเรียนแผนก IT ได้ไหม", avg_logprob=-0.4)]],
    }
    service = SpeechToTextService(THAI_CONFIG)
    assert service.transcribe(one_second()) == "สมัครเรียนแผนก IT ได้ไหม"


def test_when_both_look_fine_the_english_one_must_clearly_win():
    from audio.speech_to_text import choose_transcript

    assert choose_transcript("How much is the fee", -0.50, "ฮาว มัช อิส เดอะ ฟี", -0.55) == "th"  # too close: Thai
    assert choose_transcript("How much is the fee", -0.30, "ค่าเทอมเท่าไหร่", -0.60) == "en"
    assert choose_transcript("", -9.0, "ค่าเทอมเท่าไหร่ครับ", -0.5) == "th"
    assert choose_transcript("Where is the toilet", -0.4, "", -9.0) == "en"
    assert choose_transcript("Hmm", -0.5, "ค่าเทอมเท่าไหร่ครับ", -0.3) == "th"  # one word is not English speech
    assert choose_transcript("สวัสดี hello", -0.3, "สวัสดีครับ", -0.4) == "th"  # Thai script in the English pass: not English


def test_a_clear_language_still_costs_one_transcription_only(fake_whisper):
    fake_whisper.heard = "en"
    fake_whisper.results = [[FakeSegment(" Where is the library", avg_logprob=-0.3)]]
    service = SpeechToTextService(THAI_CONFIG)
    service.transcribe(one_second())
    loaded = models(fake_whisper)
    assert loaded["thai-medium"].calls == [] and len(loaded["small"].calls) == 1
