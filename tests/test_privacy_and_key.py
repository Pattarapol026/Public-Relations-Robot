"""Privacy notice on the visitor screen, and changing the Gemini API key from Admin."""
from __future__ import annotations

import logging
import time

import pytest
from PySide6.QtWidgets import QApplication

from app.ai_key import AiKeyController, ApiKeyError, write_env_value
from app.application import Application
from app.content_controller import ContentError
from app.logging_setup import SecretRedactionFilter
from ui.fonts import BUNDLED_FAMILY, apply_app_font
from ui.log_bridge import QtLogHandler

GOOD_KEY = "AIzaSyTestKey_1234567890abcd"


class FakeClient:
    """Stands in for GeminiClient; a key containing "bad" is refused like a wrong key."""

    def __init__(self, key: str) -> None:
        self.key = key

    def generate(self, system_instruction, turns, max_output_tokens=400, temperature=0.3) -> str:
        from ai.gemini_client import GeminiError

        if "bad" in self.key:
            raise GeminiError("auth", "HTTP 400")
        return "สวัสดีครับผู้ดูแล"


@pytest.fixture(scope="module")
def qt_app():
    qt_app = QApplication.instance() or QApplication([])
    assert apply_app_font(qt_app) == BUNDLED_FAMILY
    return qt_app


@pytest.fixture
def application(qt_app, settings, tmp_path):
    env = tmp_path / ".env"
    env.write_text("# settings\nAI_ENABLED=true\nGEMINI_API_KEY=\nTTS_SPEED=1.0\n", encoding="utf-8")
    application = Application(settings, QtLogHandler(), env_path=env)
    application.ai_key._factory = FakeClient
    yield application
    application.shutdown()
    application.window.deleteLater()


def wait_until(qt_app, condition, timeout_s: float = 10.0) -> None:
    deadline = time.monotonic() + timeout_s
    while not condition():
        assert time.monotonic() < deadline, "condition not reached in time"
        qt_app.processEvents()
        time.sleep(0.01)


# --- privacy notice ---


def test_privacy_notice_follows_the_settings_and_can_be_edited_or_hidden(application):
    content = application.content
    screen = application.window.user_screen
    notice = content.privacy_notice()
    assert "ไม่บันทึกภาพและเสียง" in notice and f"{application.settings.usage_retention_days} วัน" in notice
    assert screen._privacy.text() == notice and not screen._privacy.isHidden()

    content.save_privacy_notice(True, "  ระบบนี้ไม่เก็บข้อมูลส่วนบุคคล  ")
    assert screen._privacy.text() == "ระบบนี้ไม่เก็บข้อมูลส่วนบุคคล"
    content.save_privacy_notice(False, "ระบบนี้ไม่เก็บข้อมูลส่วนบุคคล")
    assert content.privacy_notice() == "" and screen._privacy.isHidden()
    with pytest.raises(ContentError):
        content.save_privacy_notice(True, "   ")


# --- API key ---


def test_env_file_keeps_its_other_lines(tmp_path):
    env = tmp_path / ".env"
    env.write_text("# comment\nGEMINI_API_KEY=old\n# GEMINI_API_KEY=example\nOTHER=1\n", encoding="utf-8")
    write_env_value(env, "GEMINI_API_KEY", "new")
    assert env.read_text(encoding="utf-8") == "# comment\nGEMINI_API_KEY=new\n# GEMINI_API_KEY=example\nOTHER=1\n"
    write_env_value(env, "MISSING", "x")
    assert env.read_text(encoding="utf-8").endswith("OTHER=1\nMISSING=x\n")


def test_key_is_tested_saved_and_used_without_a_restart(qt_app, application, tmp_path):
    ai_key = application.ai_key
    service = application.conversation_service
    assert not service.uses_gemini
    results = []
    ai_key.test_finished.connect(lambda ok, message: results.append((ok, message)))

    with pytest.raises(ApiKeyError):
        ai_key.test("short")
    ai_key.test("AIzaSy_bad_key_0000000000")
    wait_until(qt_app, lambda: results)
    assert results[-1][0] is False and "ไม่ถูกต้อง" in results[-1][1]
    ai_key.test(GOOD_KEY)
    wait_until(qt_app, lambda: len(results) == 2)
    assert results[-1] == (True, "ใช้งานได้ — AI ตอบว่า: สวัสดีครับผู้ดูแล")
    assert not service.uses_gemini  # testing does not switch over

    ai_key.save(GOOD_KEY)
    assert service.uses_gemini
    env = (tmp_path / ".env").read_text(encoding="utf-8")
    assert f"GEMINI_API_KEY={GOOD_KEY}" in env and "TTS_SPEED=1.0" in env
    assert ai_key.masked_key.endswith("abcd") and GOOD_KEY not in ai_key.masked_key
    assert application.window.admin_screen._ai_tag.text() == "AI: GEMINI"

    record = logging.LogRecord("x", logging.INFO, "", 0, f"key={GOOD_KEY}", None, None)
    SecretRedactionFilter([]).filter(record)
    assert GOOD_KEY not in record.getMessage()  # a key entered later is hidden in logs too

    ai_key.remove()
    assert not service.uses_gemini
    assert "GEMINI_API_KEY=\n" in (tmp_path / ".env").read_text(encoding="utf-8")


def test_without_an_env_file_the_key_cannot_be_written(qt_app, settings):
    application = Application(settings, QtLogHandler())
    try:
        assert not application.ai_key.can_edit
        with pytest.raises(ApiKeyError, match=".env"):
            application.ai_key.save(GOOD_KEY)
    finally:
        application.shutdown()
        application.window.deleteLater()


def test_api_key_section_clears_its_field_when_admin_leaves(application):
    screen = application.window.admin_screen
    screen._api_key_section.key_field.setText(GOOD_KEY)
    screen.reset()
    assert screen._api_key_section.key_field.text() == ""


def test_controller_needs_no_window(qt_app, settings, tmp_path):
    from PySide6.QtCore import QThreadPool

    from services.conversation_service import ConversationService

    service = ConversationService(lambda: [])
    controller = AiKeyController(settings, service, tmp_path / ".env", QThreadPool(), FakeClient)
    controller.save(GOOD_KEY)
    assert service.uses_gemini == settings.ai_enabled
