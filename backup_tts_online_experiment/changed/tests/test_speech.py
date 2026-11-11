"""Speech output: text splitting, the TTS wrapper, the playback queue, and speaking in the app."""
from __future__ import annotations

import sys
import time
import types

import numpy as np
import pytest
from PySide6.QtWidgets import QApplication

from app.application import Application
from app.conversation_controller import MODE_VOICE
from app.speech_controller import MID_SENTENCE_PAUSE_S, PIECE_PAUSE_S, VOICE_CHANGE_PAUSE_S, SpeechStatus
from app.state import ConversationState
from audio.microphone import AudioDevice
from audio import text_to_speech
from audio.text_to_speech import (
    TextToSpeechError,
    TextToSpeechService,
    TtsConfig,
    english_voice_rate,
    say_as_spoken,
    say_mai_yamok,
    speech_language,
    speech_pieces,
    split_for_speech,
)
from config.settings import load_settings
from services import audio_playback_service
from services.audio_playback_service import AudioPlaybackService, PlaybackError
from ui.fonts import apply_app_font
from ui.log_bridge import QtLogHandler

# --- splitting ---


def test_short_text_is_one_piece_and_sentences_are_separated():
    assert split_for_speech("สวัสดีครับ") == ["สวัสดีครับ"]
    assert split_for_speech("Hello there. How are you?\nFine!") == ["Hello there.", "How are you?", "Fine!"]
    assert split_for_speech("   \n ") == []


def test_long_thai_text_is_cut_at_spaces_within_the_limit():
    text = " ".join(["วิทยาลัยของเรามีหลายแผนกวิชา"] * 12)  # no sentence punctuation at all
    pieces = split_for_speech(text, max_chars=80)
    assert len(pieces) > 1 and all(len(piece) <= 80 for piece in pieces)
    assert " ".join(pieces) == text  # nothing lost, nothing cut inside a phrase
    unbroken = "ก" * 300
    assert all(len(piece) <= 80 for piece in split_for_speech(unbroken, max_chars=80))


def test_links_and_markup_are_not_read_aloud():
    assert split_for_speech("ดูที่ https://example.com/a?b=1 ได้เลย **ครับ**") == ["ดูที่ ได้เลย ครับ"]


def test_a_long_thai_run_without_spaces_is_cut_between_words():
    text = "วิทยาลัยของเราเปิดสอนหลายแผนกวิชาตั้งแต่ระดับประกาศนียบัตรวิชาชีพถึงปริญญาตรีครับ"
    pieces = split_for_speech(text, max_chars=40, first_max_chars=None)
    assert len(pieces) > 1 and "".join(pieces) == text
    words = ["วิทยาลัย", "ของ", "เรา", "เปิด", "สอน", "หลาย", "แผนก", "วิชา", "ตั้งแต่", "ระดับ",
             "ประกาศนียบัตร", "วิชาชีพ", "ถึง", "ปริญญาตรี", "ครับ"]
    boundaries = {len("".join(words[:n])) for n in range(len(words) + 1)}
    position = 0
    for piece in pieces:
        position += len(piece)
        assert position in boundaries  # no piece ends inside a word
    assert pieces[-1].endswith("ครับ")


def test_the_last_word_is_never_left_as_a_piece_of_its_own():
    text = " ".join(["ประโยคยาวสำหรับทดสอบการแบ่งข้อความ"] * 2) + " ครับ"
    pieces = split_for_speech(text, max_chars=len(text) - 3, first_max_chars=None)
    assert " ".join(pieces) == text
    assert pieces[-1] != "ครับ" and pieces[-1].endswith("ครับ")


def test_an_abbreviation_does_not_end_a_sentence():
    whole = "ติดต่อ ดร. สมชาย ในปี พ.ศ. 2568 ครับ"
    assert split_for_speech(whole, first_max_chars=None) == [whole]  # one sentence (the short first piece cuts it)
    assert split_for_speech("Please see Dr. Smith. He is in.") == ["Please see Dr. Smith.", "He is in."]
    assert split_for_speech("ขอบคุณค่ะ. ยินดีครับ") == ["ขอบคุณค่ะ.", "ยินดีครับ"]
    assert split_for_speech("รอสักครู่... ยินดีครับ") == ["รอสักครู่...", "ยินดีครับ"]


# --- choosing the voice ---


def test_the_voice_follows_the_main_language_of_the_text():
    assert speech_language("Where is the canteen?") == "en"
    assert speech_language("สมัครเรียน IT ได้ที่ไหนครับ") == "th"
    assert speech_language("12345") == "th"
    assert speech_language("") == "th"


def test_a_thai_answer_keeps_one_voice_and_english_is_never_given_to_the_thai_voice():
    assert speech_pieces("ติดต่อแผนก IT Support ได้เลยครับ") == [("ติดต่อแผนก IT Support ได้เลยครับ", "th")]
    assert speech_pieces("Hello there. How are you?") == [("Hello there.", "en"), ("How are you?", "en")]
    assert speech_pieces("Please visit ฝ่ายทะเบียน at the main building.") == [
        ("Please visit", "en"), ("ฝ่ายทะเบียน", "th"), ("at the main building.", "en"),
    ]
    assert speech_pieces("Please ask at the office of ฝ่ายทะเบียน.") == [
        ("Please ask at the office of", "en"), ("ฝ่ายทะเบียน", "th"),
    ]
    long_english = "Please contact the registration office at the main building for more details about it."
    assert speech_pieces(long_english) == [(long_english, "en")]  # Thai's short first piece is not for English


def test_english_voice_rate_means_what_the_windows_voice_expects():
    assert english_voice_rate(1.0) == 0
    assert english_voice_rate(0.9) == -1  # measured: 0.92 of the normal speed
    assert english_voice_rate(3.0) == 10
    assert english_voice_rate(0.1) == -10  # the voice has no slower setting


# --- TTS wrapper (fake PyThaiTTS) ---


class FakePyThaiTts:
    instances: list["FakePyThaiTts"] = []
    fail_on_load = False
    max_chars = 1000

    def __init__(self, pretrained=None) -> None:
        if FakePyThaiTts.fail_on_load:
            raise OSError("no network")
        self.pretrained = pretrained
        self.model = types.SimpleNamespace(sample_rate=24_000)
        self.calls: list[tuple[str, dict]] = []
        FakePyThaiTts.instances.append(self)

    def tts(self, text, **options):
        self.calls.append((text, options))
        if "ล้มเหลว" in text:
            raise RuntimeError("synthesis crashed")
        if len(text) > self.max_chars:
            raise ValueError("Text produces too many phonemes")
        return np.full(len(text) * 100, 0.25, dtype=np.float64)


@pytest.fixture
def fake_pythaitts(monkeypatch):
    FakePyThaiTts.instances = []
    FakePyThaiTts.fail_on_load = False
    module = types.ModuleType("pythaitts")
    module.TTS = FakePyThaiTts
    monkeypatch.setitem(sys.modules, "pythaitts", module)
    return FakePyThaiTts


def test_model_is_loaded_once_and_repeated_text_comes_from_cache(fake_pythaitts):
    service = TextToSpeechService(TtsConfig(speed=1.2))
    assert not service.is_loaded
    first = service.synthesize("สวัสดีครับ")
    again = service.synthesize("สวัสดีครับ")
    other = service.synthesize("ยินดีต้อนรับ")
    assert first.dtype == np.float32 and first.ndim == 1 and len(first) == 1000
    assert again is first and len(other) == 1200
    assert service.sample_rate == 24_000

    assert len(fake_pythaitts.instances) == 1
    model = fake_pythaitts.instances[0]
    assert model.pretrained == "fastthaig2p"
    assert model.calls == [
        ("สวัสดีครับ", {"return_type": "waveform", "speed": 1.2}),
        ("ยินดีต้อนรับ", {"return_type": "waveform", "speed": 1.2}),
    ]
    assert len(service.synthesize("  ")) == 0 and len(model.calls) == 2


def test_speed_can_change_while_running_and_is_part_of_the_cache_key(fake_pythaitts):
    service = TextToSpeechService(TtsConfig(speed=1.0))
    service.synthesize("สวัสดีครับ")
    service.synthesize("สวัสดีครับ", speed=0.8)  # the Admin's "try it" at an unsaved speed
    service.set_speed(1.3)
    service.synthesize("สวัสดีครับ")
    service.synthesize("สวัสดีครับ", speed=0.8)  # cached
    speeds = [options["speed"] for _text, options in fake_pythaitts.instances[0].calls]
    assert speeds == [1.0, 0.8, 1.3]
    assert service.speed == 1.3


def test_tts_failures_become_text_to_speech_errors(fake_pythaitts):
    fake_pythaitts.fail_on_load = True
    service = TextToSpeechService(TtsConfig())
    with pytest.raises(TextToSpeechError):
        service.load()
    fake_pythaitts.fail_on_load = False
    service.load()  # a later attempt can succeed
    with pytest.raises(TextToSpeechError):
        service.synthesize("ประโยคนี้ล้มเหลว")
    assert len(service.synthesize("ประโยคนี้ปกติ")) > 0


def test_closing_punctuation_never_reaches_the_thai_model(fake_pythaitts):
    service = TextToSpeechService(TtsConfig())
    service.synthesize("สวัสดีครับ.")
    service.synthesize("สบายดีไหมครับ?!")
    service.synthesize("ติดต่อ พ.ศ.")  # an abbreviation's dot changes how it is read: it stays
    assert len(service.synthesize(" ... ")) == 0
    assert [text for text, _options in fake_pythaitts.instances[0].calls] == ["สวัสดีครับ", "สบายดีไหมครับ", "ติดต่อ พ.ศ."]


def test_text_with_too_many_phonemes_is_divided_and_nothing_is_lost(fake_pythaitts, monkeypatch):
    monkeypatch.setattr(FakePyThaiTts, "max_chars", 30)
    service = TextToSpeechService(TtsConfig())
    text = " ".join(["ปีการศึกษา 2568"] * 6)
    audio = service.synthesize(text)
    spoken = [said for said, _options in fake_pythaitts.instances[0].calls if len(said) <= 30]
    assert " ".join(spoken) == text
    assert len(audio) == sum(len(said) * 100 for said in spoken)


def test_phone_numbers_are_read_digit_by_digit_and_other_numbers_are_left_alone(fake_pythaitts):
    service = TextToSpeechService(TtsConfig())
    service.synthesize("โทร 02-123-4567 ครับ")
    service.synthesize("มือถือ 081 234 5678")
    service.synthesize("ปี 2568 ค่าเทอม 12500 บาท วันที่ 01-02-2568")
    said = [text for text, _options in fake_pythaitts.instances[0].calls]
    assert said[0] == "โทร ศูนย์ สอง หนึ่ง สอง สาม สี่ ห้า หก เจ็ด ครับ"
    assert said[1] == "มือถือ ศูนย์ แปด หนึ่ง สอง สาม สี่ ห้า หก เจ็ด แปด"
    assert said[2] == "ปี 2568 ค่าเทอม 12500 บาท วันที่ 01-02-2568"


def test_terms_the_thai_voice_cannot_read_are_said_as_they_sound(fake_pythaitts):
    """It knows only Thai sounds and reads the dashes of "ว-สอศ-2" as "ลบ"; the screen keeps the text."""
    service = TextToSpeechService(TtsConfig())
    service.synthesize("ส่งแบบ ว-สอศ-2 เป็นไฟล์ PDF ที่ Thaiinvention.net สำหรับ ปวช. และ ปวส. ของ สอศ.")
    [(said, _options)] = fake_pythaitts.instances[0].calls
    assert said == "ส่งแบบ วอ สอศอ 2 เป็นไฟล์ พีดีเอฟ ที่ ไทยอินเวนชันดอตเน็ต สำหรับ ปอวอชอ และ ปอวอสอ ของ สอศอ"
    assert say_as_spoken(said) == said  # a piece cut again is not changed twice


def test_mai_yamok_repeats_only_the_word_before_it(fake_pythaitts):
    """Thai has no spaces between words: repeating the whole run read "ข้อมูลอื่นข้อมูลอื่น"."""
    assert say_mai_yamok("ข้อมูลอื่นๆ ครับ") == "ข้อมูลอื่นอื่น ครับ"
    assert say_mai_yamok("เดินเข้ามาใกล้ ๆ แล้วพูดได้เลย") == "เดินเข้ามาใกล้ใกล้ แล้วพูดได้เลย"
    assert say_mai_yamok("ช้าๆ ชัดๆ") == "ช้าช้า ชัดชัด"
    service = TextToSpeechService(TtsConfig())
    service.synthesize("เกษตร อาหาร และอื่นๆครับ")
    [(said, _options)] = fake_pythaitts.instances[0].calls
    assert said == "เกษตร อาหาร และอื่นอื่นครับ"


class FakeRun:
    """Stands in for the PowerShell process of the English voice."""

    def __init__(self, stdout=b"\x00\x40" * 50, returncode=0, raises=None) -> None:
        self.stdout, self.returncode, self.raises = stdout, returncode, raises
        self.calls: list[dict] = []

    def __call__(self, command, **options):
        self.calls.append({"command": command, **options})
        if self.raises:
            raise self.raises
        return types.SimpleNamespace(stdout=self.stdout, stderr=b"no voice", returncode=self.returncode)


def test_english_goes_to_the_windows_voice_and_never_to_the_thai_model(fake_pythaitts, monkeypatch):
    run = FakeRun()
    monkeypatch.setattr(text_to_speech.subprocess, "run", run)
    service = TextToSpeechService(TtsConfig(speed=0.9))
    text = 'Welcome! "; Remove-Item everything; #'
    audio = service.synthesize(text)
    assert fake_pythaitts.instances[0].calls == []
    assert audio.dtype == np.float32 and len(audio) == 50 and audio[25] == pytest.approx(0.5)  # (its two ends fade in and out)
    assert run.calls[0]["input"] == text.encode("utf-8")  # as data on stdin
    assert all(text not in part for part in run.calls[0]["command"])
    assert run.calls[0]["env"]["SPEECH_RATE"] == "-1"
    assert run.calls[0]["env"]["SPEECH_SAMPLE_RATE"] == "24000"
    service.synthesize(text)
    assert len(run.calls) == 1  # cached


def test_the_english_voices_long_silences_are_trimmed_and_all_its_speech_is_kept(fake_pythaitts, monkeypatch):
    silence, speech = np.zeros(12_000, dtype=np.int16), np.full(2400, 8000, dtype=np.int16)
    monkeypatch.setattr(text_to_speech.subprocess, "run", FakeRun(np.concatenate((silence, speech, silence)).tobytes()))
    audio = TextToSpeechService(TtsConfig()).synthesize("Hello there")
    edge = int(text_to_speech.ENGLISH_EDGE_S * 24_000)
    assert len(audio) == 2400 + 2 * edge
    assert (audio > 0).sum() == 2400


@pytest.mark.parametrize("written, said", [
    ("Open from 08.00 to 16.30.", "Open from 08:00 to 16:30."),
    ("The fee is 12.50 baht.", "The fee is 12.50 baht."),
    ("Version 2.5 scored 95.50 and 24.10 points", "Version 2.5 scored 95.50 and 24.10 points"),
])
def test_dotted_times_are_read_as_times_by_the_english_voice(fake_pythaitts, monkeypatch, written, said):
    run = FakeRun()
    monkeypatch.setattr(text_to_speech.subprocess, "run", run)
    TextToSpeechService(TtsConfig()).synthesize(written)
    assert run.calls[0]["input"] == said.encode("utf-8")


def test_a_silent_english_voice_gives_no_audio(fake_pythaitts, monkeypatch):
    monkeypatch.setattr(text_to_speech.subprocess, "run", FakeRun(bytes(4800)))
    assert len(TextToSpeechService(TtsConfig()).synthesize("Hello there")) == 0


@pytest.mark.parametrize("run", [
    FakeRun(returncode=3, stdout=b""),
    FakeRun(stdout=b""),
    FakeRun(raises=OSError("no powershell")),
    FakeRun(raises=text_to_speech.subprocess.TimeoutExpired("powershell", 20)),
])
def test_an_english_voice_failure_is_an_error_and_not_the_thai_voice(fake_pythaitts, monkeypatch, run):
    monkeypatch.setattr(text_to_speech.subprocess, "run", run)
    service = TextToSpeechService(TtsConfig())
    with pytest.raises(TextToSpeechError):
        service.synthesize("Hello there")
    assert fake_pythaitts.instances[0].calls == []


def test_the_same_text_is_not_mixed_up_between_voices_in_the_cache(fake_pythaitts, monkeypatch):
    monkeypatch.setattr(text_to_speech.subprocess, "run", FakeRun())
    service = TextToSpeechService(TtsConfig())
    english = service.synthesize("2568", language="en")
    thai = service.synthesize("2568", language="th")
    assert len(english) == 50 and len(thai) == 400


# --- playback queue (no audio device: the callback is driven by hand) ---


def play_out(service: AudioPlaybackService, frames: int) -> np.ndarray:
    out = np.full((frames, 1), 9.0, dtype=np.float32)
    service._on_audio(out, frames, None, None)
    return out[:, 0]


@pytest.fixture
def playback():
    service = AudioPlaybackService()
    service._stream = object()  # as if a device were open
    service._stream_rate = 24_000.0
    service._drained.clear()
    return service


def test_pieces_play_in_order_across_callback_blocks(playback):
    playback.feed(np.arange(1, 6, dtype=np.float32), 24_000)
    playback.feed(np.arange(6, 9, dtype=np.float32), 24_000)
    assert list(play_out(playback, 4)) == [1, 2, 3, 4]
    assert list(play_out(playback, 6)) == [5, 6, 7, 8, 0, 0]  # silence once the queue is empty
    assert playback.is_active  # more may still be fed

    playback.feed(np.array([9], dtype=np.float32), 24_000)
    playback.finish()
    assert playback.is_active  # the last piece has not been played yet
    assert list(play_out(playback, 3)) == [9, 0, 0]
    assert not playback.is_active


def test_finishing_with_nothing_queued_ends_at_once_and_stop_discards(playback):
    playback.finish()
    assert not playback.is_active

    playback._drained.clear()
    playback._finishing = False
    playback.feed(np.ones(100, dtype=np.float32), 24_000)
    playback._stream = None  # stop() would close a real stream here
    playback.stop()
    assert not playback.is_active and len(playback._pending) == 0


def test_audio_is_resampled_to_the_device_rate(playback):
    playback._stream_rate = 48_000.0
    playback.feed(np.ones(240, dtype=np.float32), 24_000)
    assert len(playback._pending[0]) == 480


def test_playback_counts_as_finished_only_after_the_driver_has_played_the_tail(playback):
    playback._tail_frames = 100  # what the driver still holds after the queue runs out
    playback.feed(np.ones(10, dtype=np.float32), 24_000)
    playback.finish()
    play_out(playback, 50)  # the last sample has left the queue, not the speaker
    assert playback.is_active
    play_out(playback, 50)
    assert playback.is_active
    play_out(playback, 50)
    assert not playback.is_active


def test_finish_after_the_queue_ran_dry_still_waits_for_the_driver(playback):
    # A last piece that made no sound (or finish arriving late) must not cut the one before it.
    playback._tail_frames = 100
    playback.feed(np.ones(10, dtype=np.float32), 24_000)
    play_out(playback, 10)
    playback.finish()
    assert playback.is_active
    play_out(playback, 100)
    assert not playback.is_active


def test_opening_the_stream_sets_the_tail_from_its_real_latency(monkeypatch):
    class Stream:
        latency = 0.2
        active = True

        def __init__(self, **_options) -> None:
            pass

        def start(self) -> None:
            pass

        def abort(self) -> None:
            pass

        def close(self) -> None:
            pass

    fake = types.SimpleNamespace(check_output_settings=lambda **_options: None, OutputStream=Stream)
    monkeypatch.setattr(audio_playback_service, "_sounddevice", lambda: fake)
    service = AudioPlaybackService()
    service.start(24_000)
    assert service._tail_frames == int((0.2 + audio_playback_service.TAIL_MARGIN_S) * 24_000)
    assert service.failure is None
    service.stop()


def test_a_dead_or_stalled_device_is_reported(playback, monkeypatch):
    assert playback.failure is None
    playback._stream = types.SimpleNamespace(active=False)
    assert "ลำโพง" in playback.failure
    playback._stream = types.SimpleNamespace(active=True)
    assert playback.failure is None
    playback._last_callback = time.monotonic() - audio_playback_service.STALL_S - 1
    assert "ลำโพง" in playback.failure
    playback._drained.set()
    assert playback.failure is None  # finished: nothing is wrong any more


# --- speaking in the application (fake TTS and fake speaker) ---


class FakeTts:
    sample_rate = 24_000

    def __init__(self) -> None:
        self.load_error: Exception | None = None
        self.spoken: list[str] = []
        self.languages: list[str | None] = []
        self.fail_on: str | None = None
        self.silent = False

    def load(self) -> None:
        if self.load_error:
            raise self.load_error

    def synthesize(self, text: str, speed: float | None = None, language: str | None = None) -> np.ndarray:
        if self.fail_on and self.fail_on in text:
            raise TextToSpeechError("สร้างเสียงพูดไม่สำเร็จ")
        self.spoken.append(text)
        self.languages.append(language)
        return np.zeros(0 if self.silent else 2400, dtype=np.float32)


class FakePlayback:
    """A speaker that "plays" each fed piece for a fixed short time."""

    PIECE_SECONDS = 0.15

    def __init__(self) -> None:
        self.started_with: list[str | None] = []
        self.fed = 0
        self.pieces: list[int] = []
        self.stops = 0
        self.fail_to_open = False
        self.failure: str | None = None
        self._until = 0.0
        self._open = False
        self._finishing = False

    def list_output_devices(self):
        return [AudioDevice("Fake Speaker", True), AudioDevice("ReSpeaker Out", False)]

    @property
    def is_active(self) -> bool:
        if not self._open:
            return False
        return not (self._finishing and time.monotonic() >= self._until)

    def start(self, sample_rate, device_name=None) -> None:
        if self.fail_to_open:
            raise PlaybackError("เปิดลำโพงไม่ได้")
        self.started_with.append(device_name)
        self._open, self._finishing, self._until = True, False, time.monotonic()

    def feed(self, samples, sample_rate) -> None:
        self.fed += 1
        self.pieces.append(len(samples))
        self._until = max(self._until, time.monotonic()) + self.PIECE_SECONDS

    def finish(self) -> None:
        self._finishing = True

    def stop(self) -> None:
        if self._open:
            self.stops += 1
        self._open = False


@pytest.fixture(scope="module")
def qt_app():
    qt_app = QApplication.instance() or QApplication([])
    apply_app_font(qt_app)
    return qt_app


def wait_until(qt_app, condition, timeout_s: float = 5.0) -> None:
    deadline = time.monotonic() + timeout_s
    while not condition():
        assert time.monotonic() < deadline, "condition not reached in time"
        qt_app.processEvents()
        time.sleep(0.01)


def pump(qt_app, seconds: float) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        qt_app.processEvents()
        time.sleep(0.01)


def build(qt_app, tmp_path, tts=None, playback=None, **environ):
    settings = load_settings(
        environ={
            "DATA_DIR": str(tmp_path / "data"), "LOG_DIR": str(tmp_path / "logs"),
            "MEDIA_DIR": str(tmp_path / "media"), "STT_ENABLED": "false",
            "VISION_ENABLED": "false", "TTS_ENABLED": "true", **environ,
        }
    )
    tts, playback = tts or FakeTts(), playback or FakePlayback()
    application = Application(settings, QtLogHandler(), tts=tts, playback=playback)
    return application, tts, playback


@pytest.fixture
def speech_app(qt_app, tmp_path):
    application, tts, playback = build(qt_app, tmp_path)
    wait_until(qt_app, lambda: application.speech.status is SpeechStatus.READY)
    yield application, tts, playback
    application.shutdown()
    application.window.deleteLater()


def state(application) -> ConversationState:
    return application.conversation.state.state


def ask_by_voice(application, question: str) -> None:
    """A spoken question in a conversation the visitor started: the one case that is answered aloud."""
    application.conversation.begin_session()
    application.conversation.ask(question, MODE_VOICE)


def test_answer_is_spoken_and_the_turn_ends_when_the_speaker_falls_silent(qt_app, speech_app):
    application, tts, playback = speech_app
    answer = application.settings.faq[0].answer
    finished = []
    application.conversation.turn_finished.connect(finished.append)

    ask_by_voice(application, application.settings.faq[0].question)
    wait_until(qt_app, lambda: state(application) is ConversationState.SPEAKING)
    assert application.window.user_screen.transcript_texts()[-1] == f"หุ่นยนต์: {answer}"  # text first
    wait_until(qt_app, lambda: finished)
    assert finished == [True]
    assert " ".join(tts.spoken) == " ".join(split_for_speech(answer))
    assert playback.started_with == [None] and playback.fed == len(tts.spoken)
    assert state(application) is ConversationState.IDLE


def test_long_answer_starts_playing_before_all_of_it_is_synthesised(qt_app, speech_app):
    application, tts, playback = speech_app
    text = " ".join(["ประโยคยาวสำหรับทดสอบการพูดต่อเนื่องของหุ่นยนต์"] * 12)
    assert len(split_for_speech(text)) >= 3
    assert application.speech.speak(text)
    wait_until(qt_app, lambda: playback.fed >= 1)
    assert playback.fed < len(split_for_speech(text))  # already playing, more still to come
    done = []
    application.speech.finished.connect(lambda: done.append(True))
    wait_until(qt_app, lambda: done)
    assert playback.fed == len(split_for_speech(text))


def test_stop_silences_the_robot_at_once(qt_app, speech_app):
    application, tts, playback = speech_app
    finished = []
    application.conversation.turn_finished.connect(finished.append)
    ask_by_voice(application, application.settings.faq[0].question)
    wait_until(qt_app, lambda: playback.fed >= 1)
    application.conversation.stop()
    assert playback.stops >= 1 and not application.speech.is_speaking
    pump(qt_app, 0.6)
    assert finished == [] and state(application) is ConversationState.IDLE


def test_new_question_interrupts_the_answer_being_spoken(qt_app, speech_app):
    application, tts, playback = speech_app
    ask_by_voice(application, application.settings.faq[0].question)
    wait_until(qt_app, lambda: playback.fed >= 1)
    application.conversation.ask(application.settings.faq[1].question, MODE_VOICE)
    wait_until(qt_app, lambda: len(application.window.user_screen.transcript_texts()) == 4)
    wait_until(qt_app, lambda: state(application) is ConversationState.IDLE)
    assert split_for_speech(application.settings.faq[1].answer)[-1] in tts.spoken


def test_each_piece_goes_to_the_voice_of_its_language(qt_app, speech_app):
    application, tts, playback = speech_app
    done = []
    application.speech.finished.connect(lambda: done.append(True))
    assert application.speech.speak("Please visit ฝ่ายทะเบียน at the office. Thank you very much.")
    wait_until(qt_app, lambda: done)
    # (the pieces are made side by side, so the order they were made in is not the order they are spoken in)
    assert sorted(zip(tts.spoken, tts.languages)) == sorted([
        ("Please visit", "en"), ("ฝ่ายทะเบียน", "th"), ("at the office.", "en"), ("Thank you very much.", "en"),
    ])


def test_thai_sentences_get_a_pause_between_them_but_not_before_the_first(qt_app, speech_app):
    application, _tts, playback = speech_app
    done = []
    application.speech.finished.connect(lambda: done.append(True))
    assert application.speech.speak("สวัสดีครับ ยินดีต้อนรับ. ขอบคุณที่มาเยี่ยมชมครับ")
    wait_until(qt_app, lambda: done)
    assert playback.pieces == [2400, 2400 + int(PIECE_PAUSE_S * 24_000)]


def test_a_voice_change_inside_a_sentence_gets_only_a_short_pause(qt_app, speech_app):
    application, _tts, playback = speech_app
    done = []
    application.speech.finished.connect(lambda: done.append(True))
    assert application.speech.speak("Please visit ฝ่ายทะเบียน today. Thank you.")
    wait_until(qt_app, lambda: done)
    short, long = 2400 + int(VOICE_CHANGE_PAUSE_S * 24_000), 2400 + int(PIECE_PAUSE_S * 24_000)
    assert playback.pieces == [2400, short, short, long]


def test_a_long_sentence_cut_in_two_gets_only_a_short_pause(qt_app, speech_app):
    """A breath after a sentence ("...ครับ"), not in the middle of one that was too long for one piece."""
    application, _tts, playback = speech_app
    done = []
    application.speech.finished.connect(lambda: done.append(True))
    text = "ต้องลงทะเบียนให้เสร็จไม่น้อยกว่าสามวันก่อนวันประกวด ตามวันและเวลาของแต่ละกลุ่มจังหวัดครับ ขอบคุณครับ"
    pieces = [piece for piece, _language in speech_pieces(text)]
    assert len(pieces) >= 2 and not pieces[0].endswith("ครับ")
    assert application.speech.speak(text)
    wait_until(qt_app, lambda: done)
    pauses = [round((length - 2400) / 24_000, 2) for length in playback.pieces[1:]]
    expected = [round(PIECE_PAUSE_S, 2) if before.endswith("ครับ") else round(MID_SENTENCE_PAUSE_S, 2) for before in pieces[:-1]]
    assert pauses == expected and round(MID_SENTENCE_PAUSE_S, 2) in pauses


def test_each_piece_loses_its_own_silence_so_pauses_are_even():
    from app.speech_controller import EDGE_S, _trimmed

    rate = 24_000
    speech = np.full(rate // 2, 0.2, dtype=np.float32)
    padded = np.concatenate((np.zeros(rate // 5, np.float32), speech, np.zeros(rate // 5, np.float32)))
    trimmed = _trimmed(padded, rate)
    assert len(trimmed) == len(speech) + 2 * int(EDGE_S * rate)  # a little kept, so nothing is clipped
    quiet_end = np.concatenate((speech, np.full(rate // 10, 0.006, np.float32)))  # the fading "-ับ"
    assert len(_trimmed(quiet_end, rate)) == len(quiet_end)


def test_switching_language_between_answers_switches_the_voice(qt_app, speech_app):
    application, tts, playback = speech_app
    done = []
    application.speech.finished.connect(lambda: done.append(True))
    for count, text in enumerate(["สวัสดีครับ", "Hello there.", "ขอบคุณครับ", "Thank you."], 1):
        assert application.speech.speak(text)
        wait_until(qt_app, lambda: len(done) == count)
    assert tts.languages == ["th", "en", "th", "en"]
    assert len(playback.started_with) == 4 and playback.stops >= 4  # one stream at a time


def test_speech_that_produced_no_sound_is_a_failure_not_a_success(qt_app, speech_app):
    application, tts, _playback = speech_app
    tts.silent = True
    done, failed = [], []
    application.speech.finished.connect(lambda: done.append(True))
    application.speech.failed.connect(failed.append)
    assert application.speech.speak("สวัสดีครับ")
    wait_until(qt_app, lambda: failed)
    assert done == [] and not application.speech.is_speaking
    assert application.speech.status is SpeechStatus.UNAVAILABLE


def test_a_speaker_that_dies_while_speaking_ends_the_turn_and_shows_as_unavailable(qt_app, speech_app):
    application, _tts, playback = speech_app
    done, failed = [], []
    application.speech.finished.connect(lambda: done.append(True))
    application.speech.failed.connect(failed.append)
    assert application.speech.speak("สวัสดีครับ")
    wait_until(qt_app, lambda: playback.fed >= 1)
    playback.failure = "ลำโพงหยุดทำงาน"
    wait_until(qt_app, lambda: failed)
    assert failed == ["ลำโพงหยุดทำงาน"] and done == []
    assert "ลำโพงหยุดทำงาน" in application.speech.status_text


def test_after_a_failure_the_next_success_makes_speech_ready_again(qt_app, speech_app):
    application, tts, _playback = speech_app
    tts.fail_on = "ล้มเหลว"
    failed, done = [], []
    application.speech.failed.connect(failed.append)
    application.speech.finished.connect(lambda: done.append(True))
    assert application.speech.speak("ประโยคนี้ล้มเหลว")
    wait_until(qt_app, lambda: failed)
    assert application.speech.status is SpeechStatus.UNAVAILABLE
    assert application.speech.speak("ประโยคนี้ปกติ")
    assert application.speech.status is SpeechStatus.UNAVAILABLE  # not yet: nothing has been heard
    wait_until(qt_app, lambda: done)
    assert application.speech.is_ready


def test_a_speaker_that_cannot_open_is_shown_as_unavailable_and_can_recover(qt_app, speech_app):
    application, _tts, playback = speech_app
    playback.fail_to_open = True
    assert not application.speech.speak("สวัสดีครับ")
    assert application.speech.status is SpeechStatus.UNAVAILABLE
    playback.fail_to_open = False
    done = []
    application.speech.finished.connect(lambda: done.append(True))
    assert application.speech.speak("สวัสดีครับ")
    wait_until(qt_app, lambda: done)
    assert application.speech.is_ready


def test_the_default_speed_is_slightly_below_the_voices_normal_rate(tmp_path):
    settings = load_settings(environ={"DATA_DIR": str(tmp_path)})
    assert settings.tts_speed == 0.9
    assert load_settings(environ={"DATA_DIR": str(tmp_path), "TTS_SPEED": "1.1"}).tts_speed == 1.1
    assert TtsConfig().speed == 0.9


def test_saved_speaker_is_used(qt_app, speech_app):
    application, _tts, playback = speech_app
    application.content.save_audio_settings(None, 0, "ReSpeaker Out")
    assert application.speech.speak("ทดสอบ")
    assert playback.started_with == ["ReSpeaker Out"]


def test_synthesis_failure_mid_answer_leaves_the_text_and_recovers(qt_app, speech_app):
    application, tts, playback = speech_app
    tts.fail_on = application.settings.faq[0].answer[:8]
    finished, failed = [], []
    application.conversation.turn_finished.connect(finished.append)
    application.speech.failed.connect(failed.append)
    ask_by_voice(application, application.settings.faq[0].question)
    wait_until(qt_app, lambda: finished, timeout_s=6.0)
    assert failed  # the answer really did go to the voice
    assert len(application.window.user_screen.transcript_texts()) == 2
    assert state(application) is ConversationState.IDLE


def test_speaker_that_cannot_open_falls_back_to_showing_text(qt_app, speech_app):
    application, _tts, playback = speech_app
    playback.fail_to_open = True
    finished = []
    application.conversation.turn_finished.connect(finished.append)
    ask_by_voice(application, application.settings.faq[0].question)
    wait_until(qt_app, lambda: state(application) is ConversationState.SPEAKING)
    assert application.speech.status is SpeechStatus.UNAVAILABLE  # it tried the speaker
    wait_until(qt_app, lambda: finished, timeout_s=10.0)  # the reading pause stands in for speech
    assert playback.fed == 0


def test_model_load_failure_leaves_the_app_answering_in_text(qt_app, tmp_path):
    tts = FakeTts()
    tts.load_error = TextToSpeechError("โหลดโมเดลเสียงพูดไม่สำเร็จ")
    application, _tts, playback = build(qt_app, tmp_path, tts=tts)
    try:
        wait_until(qt_app, lambda: application.speech.status is SpeechStatus.UNAVAILABLE)
        assert "โหลดโมเดลเสียงพูดไม่สำเร็จ" in application.speech.status_text
        ask_by_voice(application, application.settings.faq[0].question)
        wait_until(qt_app, lambda: state(application) is ConversationState.SPEAKING)
        assert playback.started_with == []
    finally:
        application.shutdown()
        application.window.deleteLater()


def test_speech_disabled_never_loads_or_plays(qt_app, tmp_path):
    application, tts, playback = build(qt_app, tmp_path, TTS_ENABLED="false")
    try:
        assert application.speech.status is SpeechStatus.DISABLED
        assert not application.speech.speak("ทดสอบ")
        assert tts.spoken == [] and playback.started_with == []
    finally:
        application.shutdown()
        application.window.deleteLater()


def test_terms_the_thai_voice_misread_are_said_as_they_sound():
    from audio.text_to_speech import _say_phone_numbers, say_as_spoken

    def spoken(text: str) -> str:
        return say_as_spoken(_say_phone_numbers(text))

    assert spoken("ใช้ Wi-Fi") == "ใช้ ไวไฟ"  # "Wi-Fi" was heard as "วิธี"
    assert spoken("กดปุ่ม English") == "กดปุ่ม อิงลิช"
    assert spoken("ผู้จบ ม.3") == "ผู้จบ มัธยมศึกษาปีที่ 3"
    assert spoken("โทร 1669 ครับ") == "โทร หนึ่ง หก หก เก้า ครับ"  # a hotline, digit by digit
    assert spoken("โทรแจ้ง 199") == "โทรแจ้ง หนึ่ง เก้า เก้า"
    assert spoken("ค่าสมัคร 1000 บาท ปี 2569") == "ค่าสมัคร 1000 บาท ปี 2569"  # amounts and years stay numbers
    assert "ลิฟต์" in spoken("ห้ามใช้ลิฟต์")  # already read correctly: left alone


def test_the_first_piece_is_short_and_later_pieces_grow_only_as_fast_as_they_can_be_made():
    from audio.text_to_speech import FIRST_CHUNK_CHARS, PIECE_GROWTH

    text = " ".join(["เปิดรับสมัครนักศึกษาใหม่ทุกวันทำการ"] * 12)  # a long answer with natural pauses
    pieces = split_for_speech(text)
    assert len(pieces[0]) <= FIRST_CHUNK_CHARS  # the robot starts to speak as soon as it can
    for before, after in zip(pieces, pieces[1:]):
        # one piece is synthesised while the one before it is spoken, so it may not be much longer
        assert len(after) <= max(FIRST_CHUNK_CHARS, PIECE_GROWTH * len(before)) + 1
    assert "".join(pieces).replace(" ", "") == text.replace(" ", "")  # nothing is lost
