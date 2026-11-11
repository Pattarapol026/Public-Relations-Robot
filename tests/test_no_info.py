"""An AI answer that only says "I have no information" is an unanswered question, listed to be added.

"Where is the toilet" got "I am Aira, and I do not have information about the location of the
restrooms": the right thing to say, but nobody saw that visitors ask it, so it never reached the
knowledge base. It now counts as unanswered, like the knowledge base having nothing to answer with.
"""
from __future__ import annotations

import time

import pytest
from PySide6.QtWidgets import QApplication

from app.application import Application
from app.state import ConversationState
from config.settings import load_settings
from database.models import KnowledgeItem
from services.analytics_service import KIND_UNANSWERED
from services.conversation_service import (
    SOURCE_GEMINI,
    SOURCE_NO_INFO,
    ConversationService,
    says_no_information,
)
from ui.fonts import apply_app_font
from ui.log_bridge import QtLogHandler

NO_INFO_EN = "I am Aira, and I do not have information about the location of the restrooms. Please ask a staff member."
NO_INFO_TH = "ผมยังไม่มีข้อมูลเรื่องห้องน้ำครับ สอบถามเจ้าหน้าที่ได้เลยครับ"


class ScriptedGemini:
    model = "fake"

    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.calls = 0

    def generate(self, system_instruction, turns, max_output_tokens=400, temperature=0.3) -> str:
        self.calls += 1
        return self.reply


@pytest.mark.parametrize("text, expected", [
    (NO_INFO_EN, True),
    (NO_INFO_TH, True),
    ("ผมไม่ทราบครับ", True),
    ("I'm sorry, I don't have that information yet.", True),
    ("There is no information about the price.", True),
    ("I don't know.", True),
    ("ลงทะเบียนที่ Thaiinvention.net ครับ", False),
    ("Register online at Thaiinvention.net.", False),
    ("The contest is on 11 and 12 November.", False),
    ("Please send the data files as a PDF.", False),  # "data" alone is not "no data"
    ("AI คือปัญญาประดิษฐ์ที่เรียนรู้จากข้อมูลครับ", False),
])
def test_the_answers_that_only_say_there_is_no_information_are_recognised(text, expected):
    assert says_no_information(text) is expected


def test_an_ai_answer_saying_it_has_no_information_is_marked_and_not_remembered():
    gemini = ScriptedGemini(NO_INFO_EN)
    service = ConversationService(lambda: [KnowledgeItem(1, "FAQ", "ลงทะเบียนที่ไหน", "ที่เว็บไซต์ครับ", (), True, False)],
                                  gemini=gemini)
    first = service.respond("where is the toilet")
    assert first.source == SOURCE_NO_INFO and first.answer_text == NO_INFO_EN  # still said as it is
    service.respond("where is the toilet")
    assert gemini.calls == 2  # not cached: asked again, it counts (and is listed) again

    gemini.reply = "Registration is online."
    assert service.respond("how do I register for the contest").source == SOURCE_GEMINI  # an ordinary answer


def test_the_knowledge_bases_own_answers_are_not_judged_by_their_wording():
    items = [KnowledgeItem(1, "FAQ", "งานเริ่มกี่โมง", "ผมยังไม่มีข้อมูลเวลาเริ่มครับ ถามเจ้าหน้าที่ได้เลย", (), True, False)]
    result = ConversationService(lambda: items, gemini=None).respond("งานเริ่มกี่โมง")
    assert result.source != SOURCE_NO_INFO  # the Admin wrote it; it is a knowledge-base answer


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


@pytest.fixture
def app(qt_app, tmp_path):
    settings = load_settings(environ={
        "DATA_DIR": str(tmp_path / "data"), "LOG_DIR": str(tmp_path / "logs"), "MEDIA_DIR": str(tmp_path / "media"),
        "STT_ENABLED": "false", "TTS_ENABLED": "false", "VISION_ENABLED": "false", "MIC_ARRAY_REQUIRED": "false",
    })
    gemini = ScriptedGemini(NO_INFO_EN)
    application = Application(settings, QtLogHandler(), gemini=gemini)
    yield application, gemini
    application.shutdown()
    application.window.deleteLater()


def test_such_a_question_appears_in_the_statistics_and_can_be_added_to_the_knowledge_base(qt_app, app):
    application, _gemini = app
    application.conversation.ask("where is the toilet")
    wait_until(qt_app, lambda: application.conversation.state.state is ConversationState.SPEAKING)

    summary = application.analytics.summary(1)
    assert (summary.questions, summary.answered) == (1, 0)  # it was not answered
    waiting = application.analytics.needing_attention(1)
    assert [item.question for item in waiting] == ["where is the toilet"]
    assert waiting[0].source == SOURCE_NO_INFO
    assert [i.id for i in application.analytics.search(1, kind=KIND_UNANSWERED)] == [waiting[0].id]

    tab = application.window.admin_screen._stats_tab
    tab.refresh()
    assert tab._table.rowCount() == 1
    assert (tab._table.item(0, 1).text(), tab._table.item(0, 2).text()) == ("where is the toilet", "AI ไม่มีข้อมูล")
    assert "AI ตอบว่าไม่มีข้อมูล" in tab._breakdown.text()

    # The Admin adds the answer: it leaves the list, and the robot answers it from then on.
    application.content.save_knowledge(None, "FAQ", "where is the toilet", "ข้างบ่อเต่าครับ", "toilet", True, False)
    application.analytics.resolve(waiting[0].id)
    tab.refresh()
    assert tab._table.rowCount() == 0


def test_the_visitor_sees_the_same_answer_with_the_apologetic_face(qt_app, app):
    application, _gemini = app
    screen = application.window.user_screen
    application.conversation.ask("where is the toilet")
    wait_until(qt_app, lambda: len(screen.transcript_texts()) == 2)
    assert screen.transcript_texts()[-1] == f"หุ่นยนต์: {NO_INFO_EN}"
    assert screen.face_mood() == "confused"

