"""Builds the real application offscreen and drives the main user flows."""
from __future__ import annotations

import dataclasses
from datetime import datetime
import time

import pytest
from PySide6.QtGui import QColor, QFontInfo, QImage
from PySide6.QtWidgets import QApplication, QPushButton, QWidget

from app.application import Application
from app.content_controller import ContentError
from app.state import ConversationState
from robot.interfaces import RobotState
from ui.fonts import BUNDLED_FAMILY, apply_app_font
from ui.main_window import STYLE_PATH
from ui.log_bridge import QtLogHandler
from ui.user_screen import UserScreen


@pytest.fixture(scope="module")
def qt_app():
    qt_app = QApplication.instance() or QApplication([])
    assert apply_app_font(qt_app) == BUNDLED_FAMILY
    return qt_app


@pytest.fixture
def application(qt_app, settings):
    application = Application(settings, QtLogHandler())
    application.adapter._action_time_s = 0.05
    yield application
    application.shutdown()
    application.window.deleteLater()


def wait_until(qt_app, condition, timeout_s: float = 3.0) -> None:
    deadline = time.monotonic() + timeout_s
    while not condition():
        assert time.monotonic() < deadline, "condition not reached in time"
        qt_app.processEvents()
        time.sleep(0.01)


def make_png(tmp_path, name: str = "poster.png"):
    image = QImage(64, 48, QImage.Format.Format_RGB32)
    image.fill(QColor("#1d4ed8"))
    path = tmp_path / name
    assert image.save(str(path))
    return path


def import_media(qt_app, application, path, title: str):
    results = []
    application.content.media_import_finished.connect(lambda item, error: results.append((item, error)))
    application.content.import_media(path, title, "", "")
    wait_until(qt_app, lambda: results)
    return results[0]


def test_window_opens_on_the_conversation_and_fits_a_small_screen(application):
    window = application.window
    window.show()
    assert window.isVisible()
    assert not window.in_admin_mode()
    assert window.user_screen.current_page() == UserScreen.PAGE_CHAT  # the home page is not shown any more
    labels = {button.text() for button in window.user_screen.findChildren(QPushButton)}
    assert not labels & {"หน้าหลัก", "สนทนา"}  # nothing to switch pages with
    assert not hasattr(window.user_screen, "_ai_chip")  # no "AI: Gemini" chip
    # A 1920x1080 display at 150 % scaling leaves about 1280x640 for the client area.
    hint = window.minimumSizeHint()
    assert hint.width() <= 1280 and hint.height() <= 640, hint


def test_every_text_widget_uses_the_bundled_font(qt_app, application):
    """System Thai fonts render garbled on Windows (see ui/fonts.py)."""
    assert "font-family" not in STYLE_PATH.read_text(encoding="utf-8").split("*/", 1)[1]
    application.window.user_screen._add_bubble("robot", "ข้อความที่สร้างภายหลัง")
    families = {
        QFontInfo(widget.font()).family()
        for widget in application.window.findChildren(QWidget)
    }
    assert families == {BUNDLED_FAMILY}


def test_numbers_are_shown_with_arabic_digits(application):
    """A Windows machine set to Thai would otherwise make spin boxes show ๐–๙."""
    from PySide6.QtWidgets import QAbstractSpinBox, QLabel, QLineEdit

    window = application.window
    window.enter_admin()
    thai_digits = set("๐๑๒๓๔๕๖๗๘๙")
    boxes = window.findChildren(QAbstractSpinBox)
    # What is drawn follows each widget's locale (inherited from the window), not just its text.
    assert boxes and all(box.locale().toString(8) == "8" for box in boxes)
    assert window.locale().toString(8) == "8"
    texts = [box.text() for box in boxes]
    texts += [widget.text() for widget in window.findChildren(QLabel)]
    texts += [widget.text() for widget in window.findChildren(QLineEdit)]
    assert texts and not [text for text in texts if thai_digits & set(text)]


def test_first_run_seeds_content_from_config(application, settings):
    content = application.content
    assert content.shortcut_questions() == [item.question for item in settings.faq]
    assert [a.title for a in content.announcements()] == list(settings.announcements)
    assert content.welcome_message() == settings.welcome_message
    assert application.window.user_screen._welcome_label.text() == settings.welcome_message


def test_question_flows_through_thinking_to_speaking(qt_app, application, settings):
    screen = application.window.user_screen
    state = application.conversation.state
    seen = []
    state.state_changed.connect(lambda _old, new: seen.append(new))

    application.conversation.ask(settings.faq[0].question)
    assert state.state is ConversationState.THINKING
    wait_until(qt_app, lambda: state.state is ConversationState.SPEAKING)

    assert seen == [ConversationState.THINKING, ConversationState.SPEAKING]
    assert screen.current_page() == UserScreen.PAGE_CHAT
    assert screen.transcript_texts() == [
        f"คุณ: {settings.faq[0].question}", f"หุ่นยนต์: {settings.faq[0].answer}",
    ]


def test_markup_in_a_question_is_shown_as_plain_text(qt_app, application):
    screen = application.window.user_screen
    application.conversation.ask("<b>bold</b> <img src=x>")
    wait_until(qt_app, lambda: len(screen.transcript_texts()) == 2)
    assert screen.transcript_texts()[0] == "คุณ: <b>bold</b> <img src=x>"


def test_cancelled_question_never_shows_a_late_answer(qt_app, application, settings):
    screen = application.window.user_screen
    application.conversation.ask(settings.faq[0].question)
    application.conversation.stop()
    application.pool.waitForDone(2000)
    qt_app.processEvents()
    assert application.conversation.state.state is ConversationState.IDLE
    assert screen.transcript_texts() == [f"คุณ: {settings.faq[0].question}"]


def test_admin_knowledge_edit_changes_shortcuts_and_answers(qt_app, application):
    content = application.content
    screen = application.window.user_screen
    content.save_knowledge(None, "สถานที่", "โรงอาหารอยู่ที่ไหน", "หลังอาคาร 2 ครับ",
                           "โรงอาหาร, อาหาร", True, True)
    assert "โรงอาหารอยู่ที่ไหน" in content.shortcut_questions()

    application.conversation.ask("หิวข้าว โรงอาหารไปทางไหน")
    wait_until(qt_app, lambda: len(screen.transcript_texts()) == 2)
    assert screen.transcript_texts()[1] == "หุ่นยนต์: หลังอาคาร 2 ครับ"

    with pytest.raises(ContentError):
        content.save_knowledge(None, "", "", "คำตอบ", "", True, False)
    with pytest.raises(ContentError):
        content.save_announcement(None, "  ", "", True)


def test_admin_settings_change_updates_welcome_page(application):
    application.content.save_display_settings("ยินดีต้อนรับสู่วิทยาลัย", 5, 10)
    assert application.window.user_screen._welcome_label.text() == "ยินดีต้อนรับสู่วิทยาลัย"
    assert (application.content.image_seconds(), application.content.announcement_seconds()) == (5, 10)
    with pytest.raises(ContentError):
        application.content.save_display_settings("ข้อความ", 0, 10)


def test_uploaded_image_plays_on_showcase_page(qt_app, application, tmp_path):
    item, error = import_media(qt_app, application, make_png(tmp_path), "โปสเตอร์")
    assert error == "" and item.title == "โปสเตอร์"

    screen = application.window.user_screen
    showcase = screen.showcase
    assert showcase.has_media()
    # The home page that plays it is kept but no longer shown by itself.
    assert screen.current_page() == UserScreen.PAGE_CHAT
    screen.show_page(UserScreen.PAGE_HOME)
    assert "โปสเตอร์" in showcase._caption.text()
    assert not showcase._media_view._image.pixmap().isNull()

    # Leaving the page stops playback; deleting the media empties the playlist.
    screen.show_page(UserScreen.PAGE_CHAT)
    assert showcase._media_view._image.pixmap().isNull()
    application.content.delete_media(item.id)
    assert not showcase.has_media()
    assert list(application.settings.media_dir.iterdir()) == []


def test_refused_upload_reports_a_reason_and_stores_nothing(qt_app, application, tmp_path):
    fake = tmp_path / "fake.mp4"
    fake.write_bytes(b"this is not a video" * 10)
    item, error = import_media(qt_app, application, fake, "ปลอม")
    assert item is None
    assert ".mp4" in error
    assert application.content.media() == []


def test_admin_actions_and_robot_commands_reach_the_activity_log(qt_app, application):
    results = []
    application.robot.command_finished.connect(results.append)
    application.content.save_announcement(None, "ประกาศใหม่", "", True)
    application.robot.send("action", "turn_left", source="touch")
    application.robot.send("action", "dance", source="touch")
    wait_until(qt_app, lambda: len(results) == 2)
    application.robot.stop(source="admin")

    entries = [(e.actor, e.action, e.detail) for e in application.content.recent_activity()]
    assert ("admin", "robot.stop", "delivered") in entries
    assert ("touch", "robot.action", "target=dance result=rejected/not_allowed") in entries
    assert any(e[1] == "robot.action" and "target=turn_left" in e[2] for e in entries)
    assert any(e[1] == "announcement.add" and "ประกาศใหม่" in e[2] for e in entries)


def test_robot_command_from_ui_moves_mock_robot(qt_app, application):
    results = []
    application.robot.command_finished.connect(results.append)
    application.robot.send("action", "turn_left", source="touch")
    wait_until(qt_app, lambda: results)
    assert results[0].ok
    assert "เรียบร้อย" in application.window.user_screen._message_bar.text()


def test_disconnected_robot_rejects_commands_and_ui_shows_it(qt_app, application):
    results = []
    application.robot.command_finished.connect(results.append)
    application.adapter.simulate_connection_loss(True)
    application.robot.send("action", "turn_left", source="touch")
    wait_until(qt_app, lambda: results)
    assert results[0].code == "robot_disconnected"
    wait_until(qt_app, lambda: application.robot.status.state is RobotState.DISCONNECTED)
    # The chip is the real robot's (MQTT), not set up in tests: not ready, and never "(จำลอง)".
    assert application.window.user_screen._robot_chip.text() == "หุ่นยนต์: ไม่พร้อม"


def test_idle_timeout_leaves_admin_and_clears_session(qt_app, application, settings):
    window = application.window
    application.conversation.ask(settings.faq[0].question)
    wait_until(qt_app, lambda: application.conversation.state.state is ConversationState.SPEAKING)
    window.enter_admin()
    assert window.in_admin_mode()
    assert application.conversation.state.state is ConversationState.IDLE  # silenced

    window._on_idle()
    assert window.in_admin_mode()  # ADMIN_IDLE_TIMEOUT_S=0 (default): the Admin stays
    assert window._idle_timer.isActive()

    window._settings = dataclasses.replace(window._settings, admin_idle_timeout_s=600)
    window._on_idle()

    assert not window.in_admin_mode()
    assert window._idle_timer.interval() == settings.idle_timeout_s * 1000  # the visitor screen's again
    assert window.user_screen.current_page() == UserScreen.PAGE_CHAT  # where it was: no switching by itself
    # The conversation goes on (9 Nov): idle time alone does not end it. Only "หยุดสนทนา" does, and then the
    # screen is cleared when the visitor has gone and the time is up.
    assert len(window.user_screen.transcript_texts()) == 2
    window.user_screen._stop_conversation()
    window._on_idle()
    assert window.user_screen.transcript_texts() == []


def test_idle_timeout_clears_the_conversation_but_stays_on_the_page(qt_app, application, tmp_path):
    import_media(qt_app, application, make_png(tmp_path), "โปสเตอร์")
    window = application.window
    for page in (UserScreen.PAGE_CHAT, UserScreen.PAGE_HOME):
        window.user_screen.show_page(page)
        window._on_idle()
        assert window.user_screen.current_page() == page
    assert not window.user_screen.showcase._media_view._image.pixmap().isNull()  # home: media playing
