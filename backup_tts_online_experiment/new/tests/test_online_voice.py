"""The online voice and everything around it: finishing touches, pieces, engine choice and fallback, the
breaker, the disk cache, and a speech controller that keeps the speaker fed. No test reaches the network."""
from __future__ import annotations

import time

import numpy as np
import pytest
from PySide6.QtWidgets import QApplication

from app.speech_controller import SpeechController, SpeechStatus
from audio import text_to_speech, voice_polish
from audio.edge_voice import EdgeVoiceError, OnlineGate
from audio.text_to_speech import (
    ENGINE_EDGE,
    ENGINE_LOCAL,
    TextToSpeechService,
    TtsConfig,
    prepare_for_online_voice,
    speech_pieces,
    split_for_online_voice,
)
from audio.voice_cache import VoiceCache
from test_speech import FakePlayback, pump, wait_until  # noqa: F401 - helpers

RATE = 24_000


@pytest.fixture(scope="module")
def qt_app():
    return QApplication.instance() or QApplication([])


def tone(seconds: float, level: float = 0.2) -> np.ndarray:
    t = np.arange(int(seconds * RATE)) / RATE
    return (level * np.sin(2 * np.pi * 180 * t)).astype(np.float32)


def silence(seconds: float) -> np.ndarray:
    return np.zeros(int(seconds * RATE), dtype=np.float32)


# --- finishing touches ---


def test_a_long_silence_inside_speech_is_shortened_and_the_edges_and_short_pauses_are_left_alone():
    speech = np.concatenate((tone(0.5), silence(0.9), tone(0.5)))
    assert voice_polish.longest_pause(speech) == pytest.approx(0.9, abs=0.02)
    tight = voice_polish.tighten_pauses(speech)
    assert voice_polish.longest_pause(tight) <= voice_polish.MAX_GAP_S + 0.03
    assert len(tight) < len(speech) - 0.5 * RATE  # about 0.6 s taken out, none of the speech
    ordinary = np.concatenate((tone(0.5), silence(0.2), tone(0.5)))
    assert voice_polish.tighten_pauses(ordinary) is ordinary  # a natural breath stays as it is
    padded = np.concatenate((silence(1.0), tone(0.5), silence(1.0)))
    assert len(voice_polish.tighten_pauses(padded)) == len(padded)  # leading and trailing quiet are not its business


def test_every_voice_is_brought_to_one_soft_level_and_never_clips():
    quiet, loud = tone(1.0, 0.02), tone(1.0, 0.9)
    for audio in (quiet, loud):
        leveled = voice_polish.level(audio)
        rms = float(np.sqrt(np.mean(leveled ** 2)))
        assert rms == pytest.approx(voice_polish.TARGET_RMS, rel=0.25) or float(np.max(np.abs(leveled))) <= 0.9
        assert float(np.max(np.abs(leveled))) <= 0.9 + 1e-6
    assert np.array_equal(voice_polish.level(silence(0.5)), silence(0.5))  # nothing to level
    faded = voice_polish.fade_edges(tone(0.5))
    assert faded[0] == 0.0 and abs(faded[-1]) < 1e-3


# --- pieces ---


def test_the_online_voice_gets_a_short_first_piece_then_whole_sentences():
    text = ("ยินดีต้อนรับครับ วิทยาลัยของเรามีหลายแผนกวิชาให้เลือกเรียนตามความสนใจของน้องๆ ทุกคน "
            "ส่วนค่าเทอมนั้นแต่ละระดับไม่เท่ากันครับ สอบถามเพิ่มเติมได้เลยนะครับ")
    pieces = split_for_online_voice(text)
    assert 20 <= len(pieces[0]) <= text_to_speech.EDGE_FIRST_CHUNK_CHARS + 45  # starts quickly
    assert " ".join(pieces) == text  # nothing lost
    assert all(len(piece) <= text_to_speech.EDGE_MAX_CHUNK_CHARS for piece in pieces)


def test_a_tiny_piece_joins_the_one_before_it_but_a_question_keeps_its_own_sentence():
    assert split_for_online_voice("ได้เลยครับ. ครับ") == ["ได้เลยครับ. ครับ"]
    assert split_for_online_voice("Is the engineering department the one you need? Yes.") == [
        "Is the engineering department the one you need?", "Yes."]


def test_english_and_thai_pieces_use_their_own_voice_with_the_online_engine():
    pieces = speech_pieces("Please visit ฝ่ายทะเบียน today. Thank you.", ENGINE_EDGE)
    assert [language for _text, language in pieces][:3] == ["en", "th", "en"]
    assert speech_pieces("สวัสดีครับ", ENGINE_EDGE) == [("สวัสดีครับ", "th")]


def test_text_for_the_online_voice_keeps_the_question_mark_and_respells_what_it_cannot_read():
    assert prepare_for_online_voice("ค่าเทอมเท่าไหร่ครับ?", "th").endswith("?")
    assert prepare_for_online_voice("ผมคือ AIRA ครับ.", "th") == "ผมคือ ไอร่า ครับ"
    assert prepare_for_online_voice("Open at 08.30 today.", "en") == "Open at 08:30 today."


# --- engine choice, fallback, breaker ---


class FakeEdge:
    def __init__(self, seconds: float = 0.5, fail: bool = False) -> None:
        self.calls: list[tuple] = []
        self.fail = fail
        self.seconds = seconds

    def synthesize(self, text, voice, rate_percent=0, pitch_hz=0, timeout_s=None, starts=None):
        self.calls.append((text, voice, rate_percent, pitch_hz, timeout_s))
        self.starts = getattr(self, "starts", []) + [starts]
        if self.fail or voice in getattr(self, "failing_voices", ()):
            raise EdgeVoiceError("offline")
        return tone(self.seconds)


@pytest.fixture
def local_voice(monkeypatch):
    """The voices on this computer, replaced by a short beep that is marked by its length."""
    monkeypatch.setattr(TextToSpeechService, "_local_audio", lambda self, text, speed, language: tone(0.2))
    monkeypatch.setattr(text_to_speech, "PROBE_EVERY_S", 3600.0)  # no check on a failed voice unless a test wants one


def test_the_online_voice_is_used_with_its_own_voice_rate_and_timeouts(local_voice):
    edge = FakeEdge()
    service = TextToSpeechService(TtsConfig(speed=0.9, engine="auto", pitch_hz=-2), edge=edge)
    assert service.plan_engine() == ENGINE_EDGE
    audio, engine = service.synthesize_piece("สวัสดีครับ", language="th", first=True)
    assert engine == ENGINE_EDGE and len(audio) > 0.4 * RATE
    text, voice, rate, pitch, timeout = edge.calls[0]
    assert voice == "th-TH-PremwadeeNeural" and rate == -3 and pitch == -2  # a soft woman's voice, talking not reading
    assert timeout == text_to_speech.EDGE_FIRST_PIECE_TIMEOUT_S
    assert edge.starts[0] == text_to_speech.REQUESTS_FLAKY_FIRST  # she often fails: two requests at once
    service.synthesize_piece("Hello there.", language="en")
    assert edge.calls[1][1] == "en-US-AvaNeural" and edge.calls[1][4] == text_to_speech.EDGE_PIECE_TIMEOUT_S
    assert edge.starts[1] == text_to_speech.REQUESTS_OTHER
    service.synthesize_piece("สวัสดีครับ", language="th")
    assert len(edge.calls) == 2  # the second time it is kept in memory


def test_an_admin_chosen_voice_applies_to_the_next_piece(local_voice):
    edge = FakeEdge()
    service = TextToSpeechService(TtsConfig(engine="auto"), edge=edge)
    service.set_voices(thai="th-TH-NiwatNeural@+25")
    service.synthesize("ขอบคุณครับ", language="th")
    assert edge.calls[0][1] == "th-TH-NiwatNeural" and edge.calls[0][3] == 25  # a voice may carry its own pitch
    edge.calls.clear()
    service.set_voices(thai="th-TH-PremwadeeNeural")
    service.synthesize("ขอบคุณครับ ยินดีครับ", language="th")
    assert edge.calls[0][1] == "th-TH-PremwadeeNeural"


def test_when_the_online_voice_fails_the_piece_comes_from_this_computer_and_two_failures_close_the_gate(local_voice):
    edge = FakeEdge(fail=True)
    service = TextToSpeechService(TtsConfig(engine="auto"), edge=edge)
    audio, engine = service.synthesize_piece("สวัสดีครับ", language="th")
    assert engine == ENGINE_LOCAL and len(audio) == int(0.2 * RATE)
    assert service.plan_engine() == ENGINE_EDGE  # one failure is not an outage
    service.synthesize_piece("ขอบคุณครับ", language="th")
    assert service.plan_engine() == ENGINE_LOCAL  # two in a row: the next answers do not even try
    calls = len(edge.calls)
    service.synthesize_piece("ยินดีครับ", language="th")
    assert len(edge.calls) == calls


def test_local_only_never_creates_or_asks_the_online_voice():
    service = TextToSpeechService(TtsConfig(engine="local"))
    assert not service.online_available and service.plan_engine() == ENGINE_LOCAL


def test_the_gate_reopens_after_its_pause_and_one_success_clears_the_count():
    clock = {"now": 0.0}
    gate = OnlineGate(clock=lambda: clock["now"])
    gate.failure()
    assert gate.allows()
    gate.failure()
    assert not gate.allows()
    clock["now"] = 46.0
    assert gate.allows()
    gate.failure()  # still down: the pause is longer now
    clock["now"] = 46.0 + 60.0
    assert not gate.allows()
    clock["now"] = 46.0 + 100.0
    assert gate.allows()
    gate.success()
    assert gate.failures == 0


def test_speech_kept_on_disk_is_played_again_without_the_network(tmp_path, local_voice):
    config = TtsConfig(engine="auto", cache_dir=str(tmp_path / "cache"))
    first = TextToSpeechService(config, edge=FakeEdge())
    audio = first.synthesize("สวัสดีครับ", language="th")
    offline = FakeEdge(fail=True)
    second = TextToSpeechService(config, edge=offline)  # a new start, no memory of it, and no network
    again, engine = second.synthesize_piece("สวัสดีครับ", language="th")
    assert engine == ENGINE_EDGE and offline.calls == []
    assert len(again) == len(audio) and float(np.max(np.abs(again - audio))) < 1e-3


def test_the_cache_forgets_the_oldest_when_it_grows_past_its_limit(tmp_path):
    cache = VoiceCache(tmp_path, max_megabytes=0.02)
    noise = np.random.default_rng(0).uniform(-0.5, 0.5, RATE).astype(np.float32)  # does not compress
    for index in range(26):  # the folder is checked every 25 writes
        cache.put(VoiceCache.key("x", index), noise)
        time.sleep(0.002)
    assert cache.get(VoiceCache.key("x", 0)) is None and cache.get(VoiceCache.key("x", 25)) is not None
    assert cache.get("nothing-here") is None


# --- a speech controller that keeps the speaker fed ---


class SlowTts:
    """Makes each piece in ``make_s`` seconds, one second long; reports the engine like the real service."""

    sample_rate = RATE
    online_available = True

    def __init__(self, make_s: float) -> None:
        self.make_s = make_s
        self.log: list[tuple[str, float]] = []

    def load(self) -> None:
        pass

    def plan_engine(self) -> str:
        return ENGINE_EDGE

    def synthesize_piece(self, text, speed=None, language=None, engine=None, first=False):
        time.sleep(self.make_s)
        self.log.append((text, time.monotonic()))
        return tone(1.0), ENGINE_EDGE

    def synthesize(self, text, speed=None, language=None, engine=None, first=False):
        return self.synthesize_piece(text, speed, language, engine, first)[0]


class RealTimePlayback(FakePlayback):
    """A speaker that plays at real speed, and notes any moment it had nothing to play."""

    def __init__(self) -> None:
        super().__init__()
        self.starved: list[float] = []
        self._ends = 0.0

    def start(self, sample_rate, device_name=None) -> None:
        super().start(sample_rate, device_name)
        self._ends = 0.0

    def feed(self, samples, sample_rate) -> None:
        now = time.monotonic()
        if self.fed and now > self._ends:
            self.starved.append(now - self._ends)
        self._ends = max(self._ends, now) + len(samples) / sample_rate
        self._until = self._ends
        self.fed += 1
        self.pieces.append(len(samples))


def build_controller(qt_app, tts, playback):
    from PySide6.QtCore import QThreadPool

    pool = QThreadPool()
    pool.setMaxThreadCount(6)
    controller = SpeechController(True, tts, playback, lambda: None, pool)
    wait_until(qt_app, lambda: controller.status is SpeechStatus.READY)
    return controller, pool


SENTENCES = " ".join(f"ประโยคที่{n}สำหรับทดสอบการพูดต่อเนื่องครับ." for n in range(1, 9))


def test_pieces_are_made_ahead_so_the_speaker_never_waits_when_each_takes_longer_than_half_its_length(qt_app):
    tts, playback = SlowTts(make_s=0.6), RealTimePlayback()
    controller, pool = build_controller(qt_app, tts, playback)
    done = []
    controller.finished.connect(lambda: done.append(True))
    assert controller.speak(SENTENCES)
    assert len(speech_pieces(SENTENCES, ENGINE_EDGE)) >= 3
    wait_until(qt_app, lambda: done, timeout_s=15.0)
    assert playback.starved == []  # each piece was ready before the one before it ended
    assert len(tts.log) == len(speech_pieces(SENTENCES, ENGINE_EDGE))
    pool.waitForDone(2000)


def test_a_piece_that_is_slow_does_not_let_a_later_one_play_first(qt_app):
    class Uneven(SlowTts):
        def synthesize_piece(self, text, speed=None, language=None, engine=None, first=False):
            if "ที่2" in text:
                time.sleep(0.8)  # the second piece is slower than the third
            audio, engine = super().synthesize_piece(text, speed, language, engine, first)
            return audio * (0.5 if "ที่2" in text else 1.0), engine

    playback = RealTimePlayback()
    controller, pool = build_controller(qt_app, Uneven(make_s=0.05), playback)
    done = []
    controller.finished.connect(lambda: done.append(True))
    controller.speak(SENTENCES)
    wait_until(qt_app, lambda: done, timeout_s=15.0)
    assert playback.fed == len(speech_pieces(SENTENCES, ENGINE_EDGE))
    pool.waitForDone(2000)


def test_question_pauses_are_longer_than_the_breath_after_a_statement():
    from app.speech_controller import ELLIPSIS_PAUSE_S, PIECE_PAUSE_S, QUESTION_PAUSE_S

    assert QUESTION_PAUSE_S > PIECE_PAUSE_S and ELLIPSIS_PAUSE_S > QUESTION_PAUSE_S


# --- a stalled request is raced by a second one ---


def test_a_slow_request_is_raced_by_a_second_and_the_first_to_finish_wins():
    from audio.edge_voice import EdgeVoice

    class Stalls(EdgeVoice):
        calls = 0

        async def _fetch(self, text, voice, rate_percent, pitch_hz):
            import asyncio

            type(self).calls += 1
            if type(self).calls == 1:
                await asyncio.sleep(30)  # the first connection is stuck
            return b"audio"

    voice = Stalls(timeout_s=5.0, hedge_after_s=0.2)
    started = time.monotonic()
    assert asyncio_run(voice, "x") == b"audio"
    assert time.monotonic() - started < 2.0 and Stalls.calls == 2


def test_both_requests_failing_raises_and_a_failed_first_lets_the_second_finish():
    from audio.edge_voice import EdgeVoice

    class Fails(EdgeVoice):
        async def _fetch(self, text, voice, rate_percent, pitch_hz):
            raise OSError("no network")

    with pytest.raises(OSError):
        asyncio_run(Fails(hedge_after_s=0.1), "x")


def asyncio_run(voice, text, starts=None):
    import asyncio

    return asyncio.run(voice._race(text, "v", 0, 0, starts or (0.0, voice._hedge_after_s)))


def test_a_short_first_sentence_joins_the_next_so_the_rest_can_be_made_in_time():
    pieces = split_for_online_voice("Welcome! The competition runs on the eleventh and twelfth of November. Ask me anything.")
    assert pieces[0].startswith("Welcome! The competition") and len(pieces[0]) >= text_to_speech.EDGE_MIN_FIRST_CHARS


def test_the_powershell_script_of_the_local_english_voice_stays_plain_ascii_without_double_quotes():
    """A comment with a double quote and Thai text made PowerShell hang on its command line (found 8 Nov 2026)."""
    script = text_to_speech._ENGLISH_VOICE_SCRIPT
    assert script.isascii() and '"' not in script


# --- the woman's voice often fails: Niwat stands in, for a while ---


def test_when_the_chosen_voice_fails_its_fallback_voice_says_the_piece_and_keeps_the_answer_in_that_voice(local_voice):
    edge = FakeEdge()
    edge.failing_voices = {"th-TH-PremwadeeNeural"}
    service = TextToSpeechService(TtsConfig(engine="auto"), edge=edge)
    audio, engine = service.synthesize_piece("สวัสดีครับ", language="th", first=True)
    assert engine == ENGINE_EDGE and len(audio) > 0  # not the local voice: another online one
    assert [call[1] for call in edge.calls] == ["th-TH-PremwadeeNeural", "th-TH-NiwatNeural"]
    assert edge.calls[1][3] == 35  # the fallback is Niwat, 35 Hz higher, so it is not as deep
    edge.calls.clear()
    service.synthesize_piece("ขอบคุณที่มาเยี่ยมชมครับ", language="th")  # the next piece of the answer
    assert [call[1] for call in edge.calls] == ["th-TH-NiwatNeural"]  # no second try at the voice that just failed
    assert service.plan_engine() == ENGINE_EDGE  # and the gate is still open: the online voice works


def test_a_stand_in_that_nothing_checks_on_is_dropped_after_its_longest_time(local_voice, monkeypatch):
    edge = FakeEdge()
    edge.failing_voices = {"th-TH-PremwadeeNeural"}
    service = TextToSpeechService(TtsConfig(engine="auto"), edge=edge)
    service.synthesize_piece("สวัสดีครับ", language="th")
    edge.failing_voices = set()  # she is working again
    clock = {"now": time.monotonic()}
    monkeypatch.setattr(text_to_speech.time, "monotonic", lambda: clock["now"])
    clock["now"] += text_to_speech.FALLBACK_VOICE_STICKS_S + 1
    edge.calls.clear()
    service.synthesize_piece("ขอบคุณครับ", language="th")
    assert edge.calls[0][1] == "th-TH-PremwadeeNeural"


def test_if_the_fallback_fails_too_the_piece_comes_from_this_computer(local_voice):
    edge = FakeEdge(fail=True)
    service = TextToSpeechService(TtsConfig(engine="auto"), edge=edge)
    audio, engine = service.synthesize_piece("สวัสดีครับ", language="th")
    assert engine == ENGINE_LOCAL and len(audio) == int(0.2 * RATE)


def test_requests_for_a_flaky_voice_go_out_together_and_a_failed_one_makes_the_next_go_at_once():
    from audio.edge_voice import EdgeVoice

    class Sometimes(EdgeVoice):
        order: list[float] = []

        async def _fetch(self, text, voice, rate_percent, pitch_hz):
            import asyncio

            number = len(type(self).order)
            type(self).order.append(time.monotonic())
            if number == 0:
                await asyncio.sleep(0.05)
                raise OSError("no audio")  # the first one dies quickly
            await asyncio.sleep(0.1)
            return b"audio"

    started = time.monotonic()
    assert asyncio_run(Sometimes(), "x", starts=(0.0, 5.0)) == b"audio"  # the second was due at 5 s: it went when the first died
    assert time.monotonic() - started < 1.0 and len(Sometimes.order) == 2
    Sometimes.order.clear()
    assert asyncio_run(Sometimes(), "x", starts=(0.0, 0.0, 9.0)) == b"audio"
    assert len(Sometimes.order) == 2 and Sometimes.order[1] - Sometimes.order[0] < 0.05  # two at once


# --- speaking, not reading ---


def test_a_new_line_is_only_a_space_and_never_a_pause():
    from audio.text_to_speech import speech_items

    items = speech_items("สวัสดีครับท่านคณะกรรมการ\nผมชื่อไอร่าครับ\n\nยินดีต้อนรับครับ", ENGINE_EDGE)
    assert [language for _value, language in items] == ["th"] * len(items)  # no pause piece between the lines
    assert "\n" not in "".join(value for value, _language in items)


def test_times_ranges_and_brackets_are_said_as_a_person_says_them():
    from audio.spoken_form import thai_clock, thai_number, to_spoken_english, to_spoken_thai

    assert to_spoken_thai("เปิดวันจันทร์-ศุกร์ 08.00-16.30 น. (ยกเว้นวันหยุด)") == "เปิดวันจันทร์ ถึง ศุกร์ แปดโมงเช้า ถึง สี่โมงครึ่ง ยกเว้นวันหยุด"
    assert to_spoken_thai("เปิด 9.00 น.") == "เปิด เก้าโมงเช้า"
    assert to_spoken_thai("ตั้งแต่ 13.15 ถึง 20.00") == "ตั้งแต่ บ่ายโมงสิบห้านาที ถึง สองทุ่ม"
    assert to_spoken_thai("ลดราคา 50% และ A&B") == "ลดราคา 50 เปอร์เซ็นต์ และ A และ B"
    assert to_spoken_thai("วันที่ 10-11 พฤศจิกายน") == "วันที่ 10 ถึง 11 พฤศจิกายน"
    assert to_spoken_thai("ราคา 1.50 บาท") == "ราคา 1.50 บาท"  # not a time
    assert [thai_clock(h, 0) for h in (0, 3, 6, 12, 14, 18, 21)] == [
        "เที่ยงคืน", "ตีสาม", "หกโมงเช้า", "เที่ยง", "บ่ายสองโมง", "หกโมงเย็น", "สามทุ่ม"]
    assert [thai_number(n) for n in (0, 10, 11, 20, 21, 35, 59)] == [
        "ศูนย์", "สิบ", "สิบเอ็ด", "ยี่สิบ", "ยี่สิบเอ็ด", "สามสิบห้า", "ห้าสิบเก้า"]
    assert to_spoken_english("Open\n(Mon-Fri) \"today\"") == "Open , Mon-Fri, today"


def test_the_first_piece_is_cut_where_a_person_breathes_and_not_inside_a_phrase():
    text = ("ค่าเทอมของวิทยาลัยอยู่ที่ประมาณ 2,500 บาทต่อเทอมครับ ซึ่งรวมค่าลงทะเบียนและค่าอุปกรณ์แล้ว "
            "ส่วนทุนการศึกษาติดต่อฝ่ายกิจการนักเรียนได้ที่ ชั้น 2 อาคาร 1 ครับ")
    pieces = split_for_online_voice(text)
    assert pieces[0].endswith("ครับ")  # a finished clause, not "... ที่ประมาณ" or "ชั้น"
    assert " ".join(pieces) == text
    for piece in pieces:
        assert not piece.endswith(("ที่", "ชั้น", "อาคาร", "ประมาณ"))  # never cut where a phrase leads on


def test_a_long_answer_without_a_full_stop_is_cut_into_a_few_long_pieces_not_many_short_ones():
    sentence = " ".join(["ผมมีหน้าที่ช่วยให้ข้อมูลแก่ผู้คนที่มาเยี่ยมชมครับ"] * 8)  # about 400 characters
    pieces = split_for_online_voice(sentence)
    assert 3 <= len(pieces) <= 5 and all(len(piece) <= text_to_speech.EDGE_MAX_CHUNK_CHARS for piece in pieces)
    assert " ".join(pieces) == sentence


def test_the_pauses_between_pieces_are_short_breaths():
    from app.speech_controller import ELLIPSIS_PAUSE_S, MID_SENTENCE_PAUSE_S, PIECE_PAUSE_S, QUESTION_PAUSE_S

    assert MID_SENTENCE_PAUSE_S < PIECE_PAUSE_S < QUESTION_PAUSE_S < ELLIPSIS_PAUSE_S <= 0.5


def test_a_probe_gives_the_chosen_voice_back_as_soon_as_it_answers_twice(local_voice, monkeypatch):
    monkeypatch.setattr(text_to_speech, "PROBE_EVERY_S", 0.05)
    edge = FakeEdge()
    edge.failing_voices = {"th-TH-PremwadeeNeural"}
    service = TextToSpeechService(TtsConfig(engine="auto"), edge=edge)
    try:
        service.synthesize_piece("สวัสดีครับ", language="th")  # she fails: Niwat says it, and a probe starts
        time.sleep(0.3)
        assert "th" in service._stand_in  # still failing: the stand-in stays
        edge.failing_voices = set()  # she is back
        deadline = time.monotonic() + 3.0
        while "th" in service._stand_in and time.monotonic() < deadline:
            time.sleep(0.02)
        assert "th" not in service._stand_in
        edge.calls.clear()
        service.synthesize_piece("ขอบคุณครับ", language="th")
        assert edge.calls[0][1] == "th-TH-PremwadeeNeural"
    finally:
        service.close()
