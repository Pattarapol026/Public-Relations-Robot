"""The small human sounds: the marks in the text, the library of takes, the rules for when to use one, playing them."""
from __future__ import annotations

import random
import time
import wave

import numpy as np
import pytest
from PySide6.QtCore import QThreadPool
from PySide6.QtWidgets import QApplication

from app.speech_controller import AFTER_CUE_PAUSE_S, SpeechController, SpeechStatus
from audio.cues import CueLibrary
from audio.text_to_speech import ENGINE_EDGE, LANG_CUE, LANG_PAUSE, speech_items, speech_pieces, strip_markup
from services.conversation_service import (
    SOURCE_FALLBACK,
    SOURCE_GEMINI,
    SOURCE_KNOWLEDGE,
    SOURCE_NO_INFO,
    SOURCE_OFFLINE,
)
from services.expression import ExpressionPolicy
from test_online_voice import RATE, RealTimePlayback, SlowTts, tone, wait_until  # noqa: F401 - helpers


@pytest.fixture(scope="module")
def qt_app():
    return QApplication.instance() or QApplication([])


# --- the marks in the text ---


def test_cue_and_pause_marks_become_items_and_a_new_line_is_only_a_space():
    items = speech_items("[chuckle] ได้เลยครับ [pause:0.7] ขอบคุณครับ", ENGINE_EDGE)
    assert [(value if language in (LANG_CUE, LANG_PAUSE) else "words", language) for value, language in items] == [
        ("chuckle", LANG_CUE), ("words", "th"), ("0.7", LANG_PAUSE), ("words", "th")]
    paragraphs = speech_items("สวัสดีครับท่านคณะกรรมการ\nผมชื่อไอร่าครับ", ENGINE_EDGE)
    assert [language for _v, language in paragraphs] == ["th"]  # a new line is not a pause: one flowing piece
    assert speech_items("[hmm]", ENGINE_EDGE) == [("hmm", LANG_CUE)]
    assert speech_items("สวัสดีครับ", ENGINE_EDGE) == speech_pieces("สวัสดีครับ", ENGINE_EDGE)  # no marks: as before
    assert strip_markup("[laugh] ฮ่าๆ ได้เลย [pause] ครับ") == "ฮ่าๆ ได้เลย ครับ"


def test_a_pause_is_capped_and_marks_are_read_whatever_their_case():
    assert speech_items("[PAUSE:9] ครับ", ENGINE_EDGE)[0] == ("2.0", LANG_PAUSE)


# --- the library ---


def make_cues(folder):
    folder.mkdir(parents=True, exist_ok=True)
    for name in ("chuckle_1", "chuckle_2", "hmm_th", "hmm_en"):
        with wave.open(str(folder / f"{name}.wav"), "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(RATE)
            handle.writeframes((tone(0.3) * 32767).astype(np.int16).tobytes())
    return folder


def test_the_cue_library_picks_the_take_for_the_language_and_does_not_repeat_a_laugh(tmp_path):
    library = CueLibrary(make_cues(tmp_path / "cues"), random.Random(1))
    assert library.names() == ["chuckle", "hmm"]
    assert len(library.get("hmm", "th")) == len(library.get("hmm", "en")) == int(0.3 * RATE)
    kinds = []
    for _ in range(6):
        library.get("chuckle")
        kinds.append(library._last["chuckle"])
    assert all(a != b for a, b in zip(kinds, kinds[1:]))  # never the same take twice in a row
    assert library.get("sigh") is None and CueLibrary(tmp_path / "nothing").get("hmm") is None


# --- when to use one ---


class Clock:
    now = 1000.0

    def __call__(self):
        return self.now


def policy(chance: float = 1.0):
    clock = Clock()
    rng = random.Random()
    rng.random = lambda: 0.0 if chance >= 1.0 else 0.99  # always think, or never
    return ExpressionPolicy(clock, rng), clock


def ordinary(p, clock):
    """An answer without any sound, and time passing: so that the next one is not "right after" another."""
    p.decorate("ที่จอดรถอยู่ไหน", "ด้านหลังอาคารหนึ่งครับ", SOURCE_KNOWLEDGE)
    clock.now += 40


def test_chuckle_at_a_joke_or_a_compliment_laugh_after_a_joke_asked_for_wow_at_good_news():
    p, clock = policy()
    assert p.decorate("555 ตลกดีนะ แล้วค่าเทอมเท่าไหร่", "ประมาณสองพันห้าร้อยบาทครับ", SOURCE_GEMINI) == \
        "[chuckle] ประมาณสองพันห้าร้อยบาทครับ"
    clock.now += 40
    ordinary(p, clock)
    assert p.decorate("เล่ามุกตลกหน่อย", "มีครับ ...", SOURCE_GEMINI).endswith(" [laugh]")
    # (a third within six answers would be too many: each of these is a robot of its own)
    fresh, _ = policy()
    assert fresh.decorate("ทีมผมได้รางวัลที่ 1 มาครับ", "ยินดีด้วยครับ", SOURCE_GEMINI).startswith("[wow] ")
    fresh, _ = policy()
    assert fresh.decorate("หุ่นยนต์ตัวนี้เก่งมากเลย", "ขอบคุณครับ", SOURCE_GEMINI).startswith("[chuckle] ")


def test_aha_when_the_visitor_explains_what_they_meant():
    p, _clock = policy()
    assert p.decorate("ไม่ใช่ครับ ผมหมายถึงค่าหอพัก", "ค่าหอพักประมาณหนึ่งพันบาทครับ", SOURCE_GEMINI).startswith("[aha] ")


@pytest.mark.parametrize("question, answer, source", [
    ("555 ฉุกเฉินมีคนเจ็บ ช่วยด้วย", "โทร 1669 ครับ", SOURCE_GEMINI),
    ("555 ขำจัง ค่าเทอมเท่าไหร่", "ขออภัยครับ ผมไม่ทราบข้อมูลนี้", SOURCE_GEMINI),
    ("555 ขำจัง", "ไม่มีข้อมูลครับ", SOURCE_NO_INFO),
    ("555 ขำจัง", "อาจจะเป็นอันนี้ครับ", SOURCE_OFFLINE),
    ("555 ขำจัง", "ขอโทษครับ", SOURCE_FALLBACK),
    ("I want to complain, this is funny lol", "Please contact the office.", SOURCE_GEMINI),
])
def test_never_on_a_serious_subject_an_apology_or_an_answer_that_is_only_a_guess(question, answer, source):
    p, _clock = policy()
    assert p.decorate(question, answer, source) == answer


def test_not_twice_in_a_row_not_within_25_seconds_and_at_most_two_in_six_answers():
    p, clock = policy()
    joke = "555 ตลกดี"
    assert p.decorate(joke, "ครับ", SOURCE_GEMINI).startswith("[chuckle]")
    clock.now += 60
    assert p.decorate(joke, "ครับ", SOURCE_GEMINI) == "ครับ"  # right after one: no
    clock.now += 10
    p.decorate("ที่จอดรถ", "ครับ", SOURCE_GEMINI)
    assert p.decorate(joke, "ครับ", SOURCE_GEMINI).startswith("[chuckle]")  # 70 s and an answer between: yes
    clock.now += 60
    p.decorate("ที่จอดรถ", "ครับ", SOURCE_GEMINI)
    assert p.decorate(joke, "ครับ", SOURCE_GEMINI) == "ครับ"  # two in the last six answers already


def test_the_cooldown_alone_blocks_a_sound_within_25_seconds():
    p, clock = policy()
    joke = "555 ตลกดี"
    assert p.decorate(joke, "ครับ", SOURCE_GEMINI).startswith("[chuckle]")
    p.decorate("ที่จอดรถ", "ครับ", SOURCE_GEMINI)
    clock.now += 10
    assert p.decorate(joke, "ครับ", SOURCE_GEMINI) == "ครับ"  # 10 s since the last sound
    clock.now += 30
    assert p.decorate(joke, "ครับ", SOURCE_GEMINI).startswith("[chuckle]")  # 40 s: it may


def test_hmm_only_for_a_question_that_calls_for_thought_and_only_when_the_dice_say_so():
    question = "แผนกไหนเหมาะกับคนชอบเขียนโปรแกรมครับ"
    answer = "แผนกเทคโนโลยีสารสนเทศครับ"
    thinking, _ = policy(chance=1.0)
    assert thinking.decorate(question, answer, SOURCE_GEMINI).startswith("[hmm] ")
    never, _ = policy(chance=0.0)
    assert never.decorate(question, answer, SOURCE_GEMINI) == answer
    exact, _ = policy(chance=1.0)
    assert exact.decorate(question, answer, SOURCE_KNOWLEDGE) == answer  # a lookup: no thinking


# --- playing them ---


def build(qt_app, cues, playback):
    pool = QThreadPool()
    controller = SpeechController(True, SlowTts(make_s=0.05), playback, lambda: None, pool, cues=cues)
    wait_until(qt_app, lambda: controller.status is SpeechStatus.READY)
    return controller, pool


def test_a_cue_in_the_text_is_played_before_the_words_with_a_pause_and_tells_the_face(qt_app, tmp_path):
    playback = RealTimePlayback()
    controller, pool = build(qt_app, CueLibrary(make_cues(tmp_path / "cues"), random.Random(2)), playback)
    heard, done = [], []
    controller.cue_started.connect(heard.append)
    controller.finished.connect(lambda: done.append(True))
    controller.speak("[chuckle] ได้เลยครับ ยินดีครับ")
    wait_until(qt_app, lambda: done, timeout_s=10.0)
    assert heard == ["chuckle"] and playback.fed == 2
    cue_samples = int(0.3 * RATE) + 2 * int(0.04 * RATE)  # the take, with the little quiet kept at its ends
    assert abs(playback.pieces[0] - cue_samples) < RATE // 10
    assert playback.pieces[1] > int(AFTER_CUE_PAUSE_S * RATE)  # a breath after the laugh, then the words
    pool.waitForDone(2000)


def test_without_a_library_or_with_the_sound_missing_the_words_are_still_spoken(qt_app, tmp_path):
    for cues in (None, CueLibrary(tmp_path / "empty")):
        playback = RealTimePlayback()
        controller, pool = build(qt_app, cues, playback)
        done = []
        controller.finished.connect(lambda: done.append(True))
        assert controller.speak("[laugh] ได้เลยครับ")
        wait_until(qt_app, lambda: done, timeout_s=10.0)
        assert playback.fed == 1
        pool.waitForDone(2000)


def test_the_decorated_answer_is_what_is_spoken_and_the_plain_answer_is_what_is_shown(qt_app, tmp_path):
    """Through the whole app: the screen and the history never see the marks."""
    from app.application import Application
    from config.settings import load_settings
    from ui.log_bridge import QtLogHandler
    from services.expression import ExpressionPolicy as Policy

    settings = load_settings(environ={
        "DATA_DIR": str(tmp_path / "data"), "LOG_DIR": str(tmp_path / "logs"), "MEDIA_DIR": str(tmp_path / "media"),
        "STT_ENABLED": "false", "VISION_ENABLED": "false", "TTS_ENABLED": "false", "TTS_ENGINE": "local",
    })
    application = Application(settings, QtLogHandler())
    try:
        spoken = []
        application.speech.speak = lambda text, speed=None: spoken.append(text) or True
        application.conversation._expression = Policy()
        application.conversation.ask("555 ตลกดี " + application.settings.faq[0].question)
        wait_until(qt_app, lambda: spoken, timeout_s=10.0)
        shown = " ".join(application.window.user_screen.transcript_texts())
        assert "[chuckle]" not in shown and "[hmm]" not in shown
    finally:
        application.shutdown()
        application.window.deleteLater()
