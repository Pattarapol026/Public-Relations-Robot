"""Knowledge expiry and CSV transfer, conversation history search, and settings files."""
from __future__ import annotations

import json
from dataclasses import replace
from datetime import date, datetime, timedelta

import pytest
from PySide6.QtWidgets import QApplication

from app.application import Application
from app.content_controller import ContentError
from database.repositories import InteractionRepository, KnowledgeRepository, VisitRepository
from services.analytics_service import KIND_DISLIKED, KIND_UNANSWERED, AnalyticsService
from services.conversation_service import ConversationResult
from services.knowledge_transfer import parse_date, read_csv, thai_date
from ui.fonts import BUNDLED_FAMILY, apply_app_font
from ui.log_bridge import QtLogHandler


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


# --- dates and expiry ---


@pytest.mark.parametrize(
    ("typed", "stored"),
    [("2026-12-31", "2026-12-31"), ("31/12/2026", "2026-12-31"), ("31/12/2569", "2026-12-31"),
     ("1/2/2570", "2027-02-01"), ("", "")],
)
def test_dates_are_read_in_either_era(typed, stored):
    assert parse_date(typed) == stored


@pytest.mark.parametrize("typed", ["31-12-2026", "30/02/2569", "tomorrow"])
def test_impossible_dates_are_refused(typed):
    with pytest.raises(ValueError):
        parse_date(typed)


def test_expired_knowledge_is_kept_but_not_used(database):
    repository = KnowledgeRepository(database)
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    today = date.today().isoformat()
    repository.add("ประกาศ", "สอบกลางภาค", "เมื่อวาน", expires_on=yesterday)
    repository.add("ประกาศ", "วันนี้มีอะไร", "งานวันนี้", expires_on=today)  # still valid today
    repository.add("FAQ", "ห้องสมุด", "ชั้น 2")
    in_use = {item.question for item in repository.list(enabled_only=True)}
    assert in_use == {"วันนี้มีอะไร", "ห้องสมุด"}
    assert len(repository.list()) == 3
    expired = {item.question for item in repository.list() if item.expired(today)}
    assert expired == {"สอบกลางภาค"}
    assert thai_date("2026-12-31") == "31/12/2569"


def test_admin_enters_an_expiry_date_in_buddhist_era(application):
    content = application.content
    content.save_knowledge(None, "ประกาศ", "ปิดเทอม", "ปิดถึงสิ้นเดือน", "", True, True, "31/12/2569")
    saved = [item for item in content.knowledge() if item.question == "ปิดเทอม"][0]
    assert saved.expires_on == "2026-12-31"
    with pytest.raises(ContentError, match="วันที่"):
        content.save_knowledge(None, "", "คำถาม", "คำตอบ", "", True, False, "32/13/2569")


# --- CSV transfer ---


def test_knowledge_round_trips_through_csv_and_updates_by_question(application, tmp_path):
    content = application.content
    before = len(content.knowledge())
    content.save_knowledge(None, "FAQ", "=1+1 คืออะไร", "สอง", "เลข, บวก", True, False, "")
    path = tmp_path / "knowledge.csv"
    assert content.export_knowledge(path) == before + 1
    assert "'=1+1" in path.read_text(encoding="utf-8-sig")  # not a spreadsheet formula

    text = path.read_text(encoding="utf-8-sig")
    text = text.replace("สอง", "สองครับ") + "FAQ,โรงอาหารอยู่ไหน,หลังอาคาร 3,,ใช่,,31/12/2569\n,,ไม่มีคำถาม,,,,\n"
    path.write_text(text, encoding="utf-8-sig")
    report = content.import_knowledge(path)
    assert (report.added, report.updated) == (1, before + 1)
    assert report.errors == ["แถว %d: ต้องมีทั้งคำถามและคำตอบ" % (before + 4)]
    items = {item.question: item for item in content.knowledge()}
    assert items["=1+1 คืออะไร"].answer == "สองครับ"
    assert items["=1+1 คืออะไร"].keywords == ("เลข", "บวก")
    assert items["โรงอาหารอยู่ไหน"].show_as_shortcut and items["โรงอาหารอยู่ไหน"].expires_on == "2026-12-31"
    assert "โรงอาหารอยู่ไหน" in content.shortcut_questions()


def test_a_file_without_question_and_answer_columns_is_refused(application, tmp_path):
    path = tmp_path / "wrong.csv"
    path.write_text("ชื่อ,เบอร์\nก,1\n", encoding="utf-8")
    with pytest.raises(ContentError, match="คอลัมน์"):
        application.content.import_knowledge(path)
    path.write_bytes("คำถาม,คำตอบ\nก,ข\n".encode("cp874"))
    with pytest.raises(ValueError, match="CSV UTF-8"):
        read_csv(path)


# --- conversation history ---


def test_history_search_by_text_and_kind_newest_first(database):
    clock = [datetime(2026, 10, 15, 9, 0)]
    analytics = AnalyticsService(
        InteractionRepository(database), VisitRepository(database), clock=lambda: clock[0]
    )
    ids = []
    for minute, (question, source) in enumerate(
        [("สมัครเรียนที่ไหน", "knowledge"), ("ค่าเทอมเท่าไร", "fallback"), ("สมัครเรียนออนไลน์ได้ไหม", "gemini")]
    ):
        clock[0] = datetime(2026, 10, 15, 9, minute)
        ids.append(analytics.record_interaction(question, ConversationResult("ตอบ", "ตอบ", source), 1000, "voice"))
    assert [i.question for i in analytics.search(1, "สมัครเรียน")] == ["สมัครเรียนออนไลน์ได้ไหม", "สมัครเรียนที่ไหน"]
    assert [i.question for i in analytics.search(1, kind=KIND_UNANSWERED)] == ["ค่าเทอมเท่าไร"]

    analytics.resolve(ids[0])
    analytics.mark_wrong(ids[0])
    assert [i.id for i in analytics.search(1, kind=KIND_DISLIKED)] == [ids[0]]
    assert ids[0] in [i.id for i in analytics.needing_attention(1)]


def test_history_page_finds_and_marks_a_conversation(application):
    analytics = application.analytics
    analytics.record_interaction("ห้องพยาบาลอยู่ไหน", ConversationResult("ชั้น 1", "ชั้น 1", "gemini"), 1200, "text")
    page = application.window.admin_screen._history_tab
    page._text.setText("พยาบาล")
    page.refresh()
    assert page._table.rowCount() == 1
    page._table.selectRow(0)
    assert "ห้องพยาบาลอยู่ไหน" in page._question.text() and "ชั้น 1" in page._answer.text()
    page._mark_wrong()
    assert analytics.search(1, kind=KIND_DISLIKED)[0].question == "ห้องพยาบาลอยู่ไหน"


# --- settings file ---


def test_settings_travel_to_another_robot_without_secrets(application, tmp_path, qt_app):
    content, tuning = application.content, application.tuning
    content.save_persona(replace(content.persona(), robot_name="น้องเทค"))
    tuning.save(replace(tuning.values, tts_speed=1.3))
    path = tmp_path / "robot-settings.json"
    count = content.export_config(path)
    document = json.loads(path.read_text(encoding="utf-8"))
    assert document["format"] == "robot-reception-config" and count == len(document["settings"])
    assert document["settings"]["persona.robot_name"] == "น้องเทค"
    assert not any("password" in key or "seeded" in key for key in document["settings"])

    # Another robot: back to defaults, then import.
    content.save_persona(replace(content.persona(), robot_name="หุ่นยนต์ประชาสัมพันธ์"))
    tuning.restore_defaults()
    document["settings"]["admin_password"] = "x"  # unknown keys are ignored
    path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
    page = application.window.admin_screen._backup_tab
    assert page.import_from(path)
    assert content.persona().robot_name == "น้องเทค"
    assert tuning.values.tts_speed == 1.3
    assert application.speech._tts.speed == 1.3  # applied without a restart
    assert application.window.admin_screen._persona_tab._robot_name.text() == "น้องเทค"

    path.write_text('{"format": "something-else"}', encoding="utf-8")
    assert not page.import_from(path)
    assert "ไม่ใช่ไฟล์การตั้งค่า" in page._status.text()
