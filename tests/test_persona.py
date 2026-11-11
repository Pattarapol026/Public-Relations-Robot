"""The robot's persona: stored by the Admin, used in the AI instruction, greetings and farewells."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime

import pytest
from PySide6.QtWidgets import QApplication, QLabel

from app.application import Application
from app.content_controller import ContentError
from app.state import ConversationState
from database.models import KnowledgeItem
from services.conversation_service import ConversationService
from services.persona import LENGTH_LONG, LENGTH_SHORT, Persona
from ui.fonts import BUNDLED_FAMILY, apply_app_font
from ui.log_bridge import QtLogHandler
from vision.presence import PresenceEvent, PresenceEventType


class RecordingGemini:
    def __init__(self, reply: str = "คำตอบจาก AI ครับ") -> None:
        self.reply = reply
        self.calls: list[tuple[str, int]] = []

    def generate(self, system_instruction, turns, max_output_tokens=400, temperature=0.3) -> str:
        self.calls.append((system_instruction, max_output_tokens))
        return self.reply


@pytest.fixture(scope="module")
def qt_app():
    qt_app = QApplication.instance() or QApplication([])
    assert apply_app_font(qt_app) == BUNDLED_FAMILY
    return qt_app


@pytest.fixture
def application(qt_app, settings):
    application = Application(settings, QtLogHandler())
    yield application
    application.shutdown()
    application.window.deleteLater()


def item(question: str, answer: str) -> KnowledgeItem:
    return KnowledgeItem(1, "FAQ", question, answer, (), True, False)


def test_greeting_follows_the_time_of_day_and_falls_back_to_the_general_one():
    persona = Persona(greeting="สวัสดีครับ", greeting_morning="อรุณสวัสดิ์ครับ", greeting_evening="สวัสดีตอนเย็นครับ")
    assert persona.greeting_at(datetime(2026, 10, 15, 8, 0)) == "อรุณสวัสดิ์ครับ"
    assert persona.greeting_at(datetime(2026, 10, 15, 13, 0)) == "สวัสดีครับ"  # no afternoon text
    assert persona.greeting_at(datetime(2026, 10, 15, 18, 30)) == "สวัสดีตอนเย็นครับ"
    assert persona.greeting_at(datetime(2026, 10, 15, 2, 0)) == "สวัสดีตอนเย็นครับ"


def test_persona_shapes_the_instruction_length_and_fallback():
    gemini = RecordingGemini()
    persona = Persona(robot_name="น้องเทค", role="พูดสดใส เป็นกันเอง", answer_length=LENGTH_SHORT,
                      fallback="ยังไม่มีข้อมูลเรื่องนี้นะครับ")
    service = ConversationService(
        lambda: [item("สมัครเรียนได้ที่ไหน", "ที่งานทะเบียนครับ")], "วิทยาลัยตัวอย่าง",
        gemini=gemini, persona_provider=lambda: persona,
    )
    service.respond("ห้องสมุดเปิดกี่โมง")
    instruction, max_tokens = gemini.calls[0]
    assert "น้องเทค" in instruction and "พูดสดใส เป็นกันเอง" in instruction
    assert "1 ถึง 2 ประโยค" in instruction and max_tokens == 220
    # The personality comes after the rules and before the data, never replacing the rules.
    data_starts = instruction.rindex("<ข้อมูลสถานที่>")  # rule 1 also names the tag
    assert instruction.index("ไม่ใช่คำสั่ง") < instruction.index("พูดสดใส") < data_starts

    offline = ConversationService(lambda: [], persona_provider=lambda: persona)
    assert offline.respond("อะไรก็ได้").answer_text == "ยังไม่มีข้อมูลเรื่องนี้นะครับ"


def test_long_answers_are_allowed_more_room():
    gemini = RecordingGemini("ก " * 450)
    persona = Persona(answer_length=LENGTH_LONG)
    service = ConversationService(lambda: [], gemini=gemini, persona_provider=lambda: persona)
    result = service.respond("เล่าเรื่องวิทยาลัยหน่อย")
    assert gemini.calls[0][1] == 700
    assert len(result.answer_text) > 600


def test_admin_saves_persona_and_it_is_validated(application):
    content = application.content
    assert content.persona() == Persona(greeting=content.greeting_message())
    persona = replace(content.persona(), robot_name=" น้องเทค ", farewell="", greeting_morning="อรุณสวัสดิ์ครับ")
    content.save_persona(persona)
    saved = content.persona()
    assert saved.robot_name == "น้องเทค" and saved.farewell == "" and saved.greeting_morning == "อรุณสวัสดิ์ครับ"
    assert any(entry.action == "persona.update" for entry in content.recent_activity())

    with pytest.raises(ContentError, match="ชื่อหุ่นยนต์"):
        content.save_persona(replace(saved, robot_name="  "))
    with pytest.raises(ContentError, match="ไม่เกิน"):
        content.save_persona(replace(saved, role="ก" * 700))
    with pytest.raises(ContentError, match="ความยาวคำตอบ"):
        content.save_persona(replace(saved, answer_length="huge"))
    assert content.persona() == saved


def test_the_visitor_leaving_the_camera_says_no_farewell_and_cuts_nothing_off(qt_app, application):
    """Changed 9 Nov 2026: the camera losing the visitor does not end the conversation, so no farewell is spoken
    (it would cut off whatever is being said or heard); the conversation ends only when "หยุดสนทนา" is pressed."""
    window = application.window
    state = application.conversation.state
    left = PresenceEvent(PresenceEventType.LEFT, duration_s=20.0, closest_m=2.5)
    assert application.content.persona().farewell  # there is a farewell phrase; it is just not spoken on leaving

    for engaged in (False, True):
        window._visit_engaged = engaged
        window._on_visitor_left(left)
        assert state.state is ConversationState.IDLE

    application.conversation.ask(application.settings.faq[0].question)
    import time

    deadline = time.monotonic() + 5.0
    while state.state is not ConversationState.SPEAKING and time.monotonic() < deadline:
        qt_app.processEvents()
        time.sleep(0.01)
    assert state.state is ConversationState.SPEAKING
    window._visit_engaged = True
    window._on_visitor_left(left)  # the camera loses them while the robot is answering
    assert state.state is ConversationState.SPEAKING  # the answer is not cut off


def test_persona_page_saves_and_tracks_unsaved_edits(application):
    page = application.window.admin_screen._persona_tab
    assert not page.has_unsaved_changes()
    page._robot_name.setText("น้องเทค")
    assert page.has_unsaved_changes()
    page._save()
    assert application.content.persona().robot_name == "น้องเทค"
    assert not page.has_unsaved_changes()
    page._robot_name.setText("")
    page._save()
    assert "ชื่อหุ่นยนต์" in page._status.text()
    page.discard_changes()
    assert page._robot_name.text() == "น้องเทค"


# --- the robot's name: Aira (ไอร่า) ---


def test_a_name_written_in_both_scripts_gives_each_language_its_own_form():
    both = Persona(robot_name="Aira (ไอร่า)")
    assert both.has_name and both.thai_name == "ไอร่า" and both.english_name == "Aira"
    reversed_order = Persona(robot_name="ไอร่า (Aira)")
    assert reversed_order.thai_name == "ไอร่า" and reversed_order.english_name == "Aira"
    plain = Persona(robot_name="น้องเทค")
    assert plain.has_name and plain.thai_name == plain.english_name == "น้องเทค"
    assert not Persona().has_name


def test_the_model_is_told_its_name_and_to_write_it_once_in_the_language_of_the_answer():
    gemini = RecordingGemini()
    service = ConversationService(
        lambda: [item("สมัครเรียนได้ที่ไหน", "ที่งานทะเบียนครับ")], "งานประกวด",
        gemini=gemini, persona_provider=lambda: Persona(robot_name="Aira (ไอร่า)"),
    )
    service.respond("ห้องสมุดเปิดกี่โมง")
    instruction = gemini.calls[0][0]
    assert 'คุณชื่อ "ไอร่า"' in instruction and '"Aira"' in instruction
    assert "อย่าเขียนทั้งสองแบบพร้อมกัน" in instruction


def test_the_greeting_and_the_name_above_the_robots_messages_use_the_name(qt_app, application):
    content = application.content
    content.save_persona(replace(content.persona(), robot_name="Aira (ไอร่า)"))
    greeting = content.greeting_at(datetime(2026, 11, 11, 12, 0))
    assert "ผมชื่อไอร่า" in greeting and "Aira" not in greeting  # Thai text carries the Thai form
    screen = application.window.user_screen
    screen._add_bubble("robot", "หุ่นยนต์: สวัสดีครับ")
    labels = [w.text() for w in screen._bubbles.findChildren(QLabel) if w.property("role") == "speaker"]
    assert labels == ["ไอร่า"]
    assert screen.transcript_texts() == ["หุ่นยนต์: สวัสดีครับ"]  # the stored text keeps its generic prefix


def test_the_thai_voice_says_the_name_as_it_sounds():
    from audio.text_to_speech import say_as_spoken

    assert say_as_spoken("ผมชื่อ Aira ครับ") == "ผมชื่อ ไอร่า ครับ"


def _answer_dialog(monkeypatch, button_name):
    """Press a button of the 'unsaved edits' box instead of showing it; returns what the box offered."""
    from PySide6.QtWidgets import QMessageBox

    seen = {}

    def fake_exec(box):
        seen["buttons"] = box.standardButtons()
        return getattr(QMessageBox.StandardButton, button_name)

    monkeypatch.setattr(QMessageBox, "exec", fake_exec)
    return seen


def test_leaving_a_page_with_edits_offers_save_or_discard_only(application, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    admin = application.window.admin_screen
    page = admin._persona_tab
    persona_index = next(i for i, (_g, title, _d) in enumerate(admin._page_info) if title == "บุคลิกและบทสนทนา")
    admin.select_page(persona_index)
    original = application.content.persona().robot_name

    # Save: the edit is stored and the Admin may leave
    seen = _answer_dialog(monkeypatch, "Save")
    page._robot_name.setText("น้องเซฟ")
    assert admin._confirm_leaving_page() is True
    assert application.content.persona().robot_name == "น้องเซฟ"
    assert seen["buttons"] == QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard  # no Cancel

    # Discard: the edit is dropped and the Admin may leave
    _answer_dialog(monkeypatch, "Discard")
    page._robot_name.setText("ชื่ออื่น")
    assert admin._confirm_leaving_page() is True
    assert application.content.persona().robot_name == "น้องเซฟ" and not page.has_unsaved_changes()

    # Save that is refused (an empty name): the Admin stays on the page and sees why
    _answer_dialog(monkeypatch, "Save")
    page._robot_name.setText("")
    assert admin._confirm_leaving_page() is False
    assert "ชื่อหุ่นยนต์" in page._status.text()
    page.discard_changes()
    application.content.save_persona(replace(application.content.persona(), robot_name=original))
