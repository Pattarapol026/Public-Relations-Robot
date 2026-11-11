"""Messages on the robot screen show in full: Thai wraps between words, and the box grows to fit."""
from __future__ import annotations

import time

import pytest
from PySide6.QtWidgets import QApplication

from app.application import Application
from config.settings import load_settings
from ui.fonts import apply_app_font
from ui.log_bridge import QtLogHandler
from ui.thai_wrap import ZERO_WIDTH_SPACE, soft_wrap
from ui.user_screen import UserScreen, _Bubble

# From the contest rules; written with no spaces inside the clauses, as Thai is.
LONG = (
    "สำหรับประเภทที่ 1 ผลงานต้องผลิตขึ้นเอง ใช้งานได้จริงและปลอดภัย ไม่กระทบสิ่งแวดล้อม "
    "และต้องนำไปทดลองใช้จริงกับบุคคล ชุมชน หรือสถานประกอบการ "
    "โดยต้องเกิดจากการบูรณาการการเรียนการสอนที่สอดคล้องกับสาขาวิชาที่เรียนอยู่ครับ"
)


@pytest.fixture(scope="module")
def qt_app():
    qt_app = QApplication.instance() or QApplication([])
    apply_app_font(qt_app)
    return qt_app


def test_thai_words_get_places_to_break_and_nothing_else_changes():
    wrapped = soft_wrap("โดยต้องเกิดจากการบูรณาการการเรียนการสอนที่สอดคล้องกับสาขา")
    assert ZERO_WIDTH_SPACE in wrapped
    assert wrapped.replace(ZERO_WIDTH_SPACE, "") == "โดยต้องเกิดจากการบูรณาการการเรียนการสอนที่สอดคล้องกับสาขา"
    assert soft_wrap("Hello, how are you? 123") == "Hello, how are you? 123"
    assert soft_wrap("") == ""
    assert soft_wrap(wrapped.replace(ZERO_WIDTH_SPACE, "")) == wrapped  # same text, same breaks


def test_a_line_never_starts_with_a_mark_that_belongs_to_the_letter_before():
    wrapped = soft_wrap("เดินเข้ามาใกล้ๆแล้วพูดได้เลยนะครับ ขอบคุณค่ะ")
    for piece in wrapped.split(ZERO_WIDTH_SPACE)[1:]:
        assert piece[0] not in "ๆฯะัาำิีึืฺุูเ็่้๊๋์ํ๎" or piece[0] == "เ"  # เ starts a syllable, so it may start a line


def test_a_long_thai_sentence_with_no_spaces_wraps_instead_of_running_off_the_box(qt_app):
    sentence = "โดยต้องเกิดจากการบูรณาการการเรียนการสอนที่สอดคล้องกับสาขาวิชาที่เรียนอยู่"
    bubble = _Bubble(sentence)
    bubble.setMaximumWidth(300)
    line = bubble.fontMetrics().height()
    assert bubble.heightForWidth(300) > 2 * line  # several lines, not one that is cut off


@pytest.fixture
def screen(qt_app, tmp_path):
    settings = load_settings(environ={
        "DATA_DIR": str(tmp_path / "data"), "LOG_DIR": str(tmp_path / "logs"), "MEDIA_DIR": str(tmp_path / "media"),
        "STT_ENABLED": "false", "TTS_ENABLED": "false", "VISION_ENABLED": "false", "MIC_ARRAY_REQUIRED": "false",
    })
    application = Application(settings, QtLogHandler())
    application.window.resize(1366, 768)
    application.window.show()
    application.window.user_screen.show_page(UserScreen.PAGE_CHAT)
    yield application.window.user_screen
    application.shutdown()
    application.window.deleteLater()


def pump(qt_app, seconds: float) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        qt_app.processEvents()
        time.sleep(0.01)


def test_every_message_gets_the_height_its_text_needs_and_keeps_its_original_text(qt_app, screen):
    screen._add_bubble("user", "คุณ: ประเภทที่ 1 ต้องมีอะไรบ้าง")
    screen._add_bubble("robot", f"หุ่นยนต์: {LONG}")
    pump(qt_app, 0.6)  # the fade-in is over

    assert screen.transcript_texts() == ["คุณ: ประเภทที่ 1 ต้องมีอะไรบ้าง", f"หุ่นยนต์: {LONG}"]  # no hidden breaks
    bubbles = screen.findChildren(_Bubble)
    assert len(bubbles) == 2
    for bubble in bubbles:
        assert bubble.height() >= bubble.heightForWidth(bubble.width())  # nothing cut off at the bottom
        assert bubble.graphicsEffect() is None  # the fade-in effect is dropped when it is done
    long_bubble = bubbles[1]
    assert long_bubble.height() > 4 * long_bubble.fontMetrics().height()  # it wrapped onto several lines


def test_a_long_answer_is_shown_from_its_first_line(qt_app, screen):
    for _ in range(3):
        screen._add_bubble("user", "คุณ: ถามอะไรสักอย่าง")
        screen._add_bubble("robot", f"หุ่นยนต์: {LONG}{LONG}")
    pump(qt_app, 0.6)
    scroll = screen._transcript_scroll
    last_row = screen._latest_row
    top_in_view = last_row.y() - scroll.verticalScrollBar().value()
    assert 0 <= top_in_view < scroll.viewport().height()  # the answer's first line is on screen
