"""Usage statistics, scheduled announcements, and the Admin's view of what reaches the AI."""
from __future__ import annotations

import csv
import time
from datetime import datetime, timedelta

import pytest
from PySide6.QtWidgets import QApplication

from app.application import Application
from app.content_controller import ContentError
from app.state import ConversationState
from config.settings import load_settings
from database.models import Schedule
from database.repositories import InteractionRepository, ScheduleRepository, VisitRepository
from services.analytics_service import AnalyticsService
from services.conversation_service import ConversationResult
from services.schedule_service import (
    GRACE_MINUTES,
    describe_weekdays,
    due_schedules,
    normalize_time,
    normalize_weekdays,
)
from ui.fonts import apply_app_font
from ui.log_bridge import QtLogHandler
from ui.user_screen import UserScreen
from vision.mock_source import MockVisionSource

NOON = datetime(2026, 10, 12, 12, 0, 0)  # a Monday


class Clock:
    def __init__(self, now: datetime = NOON) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


def result(source: str, text: str = "คำตอบ", language: str = "th", ai_error=None) -> ConversationResult:
    return ConversationResult(text, text, source, language, ai_error=ai_error)


@pytest.fixture
def analytics(database):
    clock = Clock()
    service = AnalyticsService(
        InteractionRepository(database), VisitRepository(database), clock=clock, retention_days=90
    )
    return service, clock


# --- statistics ---


def test_summary_counts_questions_answers_feedback_and_visits(analytics):
    service, clock = analytics
    clock.now = NOON.replace(hour=9, minute=15)
    first = service.record_interaction("เปิดกี่โมง", result("gemini"), 1200, "voice")
    service.record_interaction("สมัครที่ไหน", result("knowledge"), 40, "shortcut")
    clock.now = NOON.replace(hour=14, minute=5)
    second = service.record_interaction("ค่าเทอมเท่าไหร่", result("fallback"), 900, "voice")
    service.record_interaction("Where is the canteen?", result("knowledge_offline", language="en", ai_error="network"), 300, "text")
    service.record_feedback(first, positive=True)
    service.record_feedback(second, positive=False)
    service.record_visit(duration_s=40, closest_m=0.8, engaged=True, questions=2)
    service.record_visit(duration_s=10, closest_m=2.6, engaged=False, questions=0)

    summary = service.summary(days=1)
    assert (summary.questions, summary.answered) == (4, 3)
    assert summary.answer_rate == 0.75
    assert (summary.feedback_up, summary.feedback_down, summary.satisfaction) == (1, 1, 0.5)
    assert summary.ai_failures == 1
    assert summary.average_response_ms == 610
    assert summary.by_source == {"gemini": 1, "knowledge": 1, "fallback": 1, "knowledge_offline": 1}
    assert summary.by_mode == {"voice": 2, "shortcut": 1, "text": 1}
    assert summary.by_language == {"th": 3, "en": 1}
    assert (summary.per_hour[9], summary.per_hour[14], sum(summary.per_hour)) == (2, 2, 4)
    assert (summary.visits, summary.engaged_visits, summary.engagement_rate) == (2, 1, 0.5)
    assert summary.average_visit_s == 25


def test_empty_period_has_no_rates_rather_than_zero_percent(analytics):
    service, _clock = analytics
    summary = service.summary(days=7)
    assert summary.questions == 0
    assert summary.answer_rate is None and summary.satisfaction is None and summary.engagement_rate is None


def test_period_is_whole_calendar_days_including_today(analytics):
    service, clock = analytics
    clock.now = NOON - timedelta(days=6, hours=11)  # early morning, six days ago
    service.record_interaction("หกวันก่อน", result("gemini"), 100, "text")
    clock.now = NOON - timedelta(days=7)
    service.record_interaction("เจ็ดวันก่อน", result("gemini"), 100, "text")
    clock.now = NOON - timedelta(days=1)
    service.record_interaction("เมื่อวาน", result("gemini"), 100, "text")
    clock.now = NOON
    assert service.summary(days=1).questions == 0
    assert service.summary(days=2).questions == 1
    assert service.summary(days=7).questions == 2
    assert service.summary(days=30).questions == 3


def test_questions_needing_attention_feed_the_knowledge_base(analytics):
    service, clock = analytics
    answered = service.record_interaction("เปิดกี่โมง", result("gemini"), 100, "voice")
    unknown = service.record_interaction("ค่าเทอมเท่าไหร่", result("fallback"), 100, "voice")
    clock.now = NOON + timedelta(minutes=5)
    disliked = service.record_interaction("รถประจำทางสายไหนผ่าน", result("gemini"), 100, "text")
    service.record_feedback(disliked, positive=False)
    service.record_feedback(answered, positive=True)

    waiting = service.needing_attention(days=7)
    assert [item.question for item in waiting] == ["รถประจำทางสายไหนผ่าน", "ค่าเทอมเท่าไหร่"]  # newest first

    service.resolve(unknown)
    assert [item.id for item in service.needing_attention(days=7)] == [disliked]


def test_question_text_is_not_stored_when_logging_is_off(database):
    service = AnalyticsService(
        InteractionRepository(database), VisitRepository(database), log_questions=False, clock=Clock()
    )
    service.record_interaction("ผมชื่อสมชาย เบอร์ 0812345678", result("fallback", "ขออภัย"), 100, "voice")
    with database.connect() as connection:
        row = connection.execute("SELECT question, answer, source FROM interactions").fetchone()
    assert (row["question"], row["answer"], row["source"]) == ("", "", "fallback")
    assert service.summary(1).questions == 1  # still counted
    assert service.needing_attention(7) == []  # nothing to show without text


def test_stored_text_can_be_erased_and_old_rows_are_purged(analytics, database):
    service, clock = analytics
    clock.now = NOON - timedelta(days=120)
    service.record_interaction("เก่ามาก", result("gemini"), 100, "text")
    service.record_visit(5, 1.0, True, 1)
    clock.now = NOON
    service.record_interaction("ใหม่", result("gemini"), 100, "text")

    assert service.purge_old() == 2
    assert service.summary(365).questions == 1
    assert service.clear_question_text() == 1
    with database.connect() as connection:
        assert connection.execute("SELECT question FROM interactions").fetchone()[0] == ""
    assert service.summary(1).questions == 1


def test_csv_export_opens_in_excel_with_thai_text(analytics, tmp_path):
    service, _clock = analytics
    rated = service.record_interaction("สมัครเรียนที่ไหน", result("gemini", "ที่งานทะเบียนครับ"), 850, "voice")
    service.record_feedback(rated, positive=True)
    path = tmp_path / "usage.csv"
    assert service.export_csv(path, days=1) == 1

    raw = path.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")  # byte-order mark: Excel reads it as UTF-8
    rows = list(csv.reader(path.read_text(encoding="utf-8-sig").splitlines()))
    assert rows[0][:3] == ["เวลา", "คำถาม", "คำตอบ"]
    assert rows[1][1:8] == ["สมัครเรียนที่ไหน", "ที่งานทะเบียนครับ", "gemini", "th", "voice", "850", "ช่วยได้"]


def test_csv_export_does_not_let_a_visitor_plant_a_spreadsheet_formula(analytics, tmp_path):
    service, _clock = analytics
    service.record_interaction('=HYPERLINK("http://example.com","กด")', result("fallback", "-ไม่มีข้อมูล"), 10, "text")
    path = tmp_path / "usage.csv"
    service.export_csv(path, days=1)
    row = list(csv.reader(path.read_text(encoding="utf-8-sig").splitlines()))[1]
    assert row[1].startswith("'=") and row[2].startswith("'-")


def test_version_2_database_is_upgraded_and_keeps_its_data(tmp_path):
    import sqlite3

    from database import db as db_module
    from database.db import SCHEMA_VERSION, Database

    path = tmp_path / "old.db"
    connection = sqlite3.connect(path)
    connection.executescript(db_module._SCHEMA + db_module._MIGRATIONS[1])  # what V0.5 created
    connection.execute("PRAGMA user_version = 2")
    connection.execute(
        "INSERT INTO knowledge_items (question, answer, created_at, updated_at) VALUES ('ถาม', 'ตอบ', 'a', 'b')"
    )
    connection.commit()
    connection.close()

    database = Database(path)
    database.initialize()
    with database.connect() as upgraded:
        assert upgraded.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        assert upgraded.execute("SELECT answer FROM knowledge_items").fetchone()[0] == "ตอบ"
        for table in ("interactions", "visits", "schedules"):
            assert upgraded.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
    database.initialize()  # starting again changes nothing


# --- schedules ---


def schedule(time_of_day="12:00", weekdays="0123456", enabled=True, last_run="") -> Schedule:
    return Schedule(1, "ประกาศ", time_of_day, weekdays, "ข้อความ", None, enabled, last_run)


def test_time_and_weekday_input_is_normalised():
    assert normalize_time("8.30") == "08:30" and normalize_time(" 16:05 ") == "16:05"
    assert normalize_time("24:00") is None and normalize_time("8:5") is None and normalize_time("เช้า") is None
    assert normalize_weekdays("6x0 4") == "046"
    assert describe_weekdays("0123456") == "ทุกวัน" and describe_weekdays("01234") == "จันทร์–ศุกร์"
    assert describe_weekdays("05") == "จ ส"


def test_schedule_is_due_from_its_time_for_the_grace_period_only():
    item = schedule("12:00")
    assert due_schedules([item], NOON - timedelta(seconds=1)) == []
    assert due_schedules([item], NOON) == [item]
    assert due_schedules([item], NOON + timedelta(minutes=GRACE_MINUTES - 1)) == [item]
    assert due_schedules([item], NOON + timedelta(minutes=GRACE_MINUTES)) == []


def test_schedule_respects_weekday_enabled_flag_and_runs_once_a_day():
    assert due_schedules([schedule(weekdays="1234")], NOON) == []  # NOON is a Monday (0)
    assert due_schedules([schedule(weekdays="0")], NOON) != []
    assert due_schedules([schedule(enabled=False)], NOON) == []
    assert due_schedules([schedule(last_run="2026-10-12")], NOON) == []
    assert due_schedules([schedule(last_run="2026-10-11")], NOON) != []
    assert due_schedules([schedule(time_of_day="bad")], NOON) == []


def test_changing_the_time_lets_a_schedule_run_again_today(database):
    repository = ScheduleRepository(database)
    schedule_id = repository.save(None, "ประกาศ", "08:00", "0123456", "ข้อความ", None, True)
    repository.mark_run(schedule_id, "2026-10-12")
    repository.save(schedule_id, "ประกาศ (แก้ชื่อ)", "08:00", "0123456", "ข้อความ", None, True)
    assert repository.list()[0].last_run_date == "2026-10-12"  # same time: still done for today
    repository.save(schedule_id, "ประกาศ (แก้ชื่อ)", "13:00", "0123456", "ข้อความ", None, True)
    assert repository.list()[0].last_run_date == ""


# --- in the application ---


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


@pytest.fixture
def stats_app(qt_app, tmp_path):
    settings = load_settings(
        environ={
            "DATA_DIR": str(tmp_path / "data"), "LOG_DIR": str(tmp_path / "logs"),
            "MEDIA_DIR": str(tmp_path / "media"), "STT_ENABLED": "false", "TTS_ENABLED": "false",
            "VISION_DWELL_SECONDS": "0.2", "VISION_GREET_DWELL_SECONDS": "0.3", "VISION_GREET_STILL_SECONDS": "0",
            "VISION_LEAVE_SECONDS": "0.4",
        }
    )
    camera = MockVisionSource()
    application = Application(settings, QtLogHandler(), vision_source=camera)
    yield application, camera
    application.shutdown()
    application.window.deleteLater()


def state(application) -> ConversationState:
    return application.conversation.state.state


def test_questions_feedback_and_visits_are_recorded_as_they_happen(qt_app, stats_app):
    application, camera = stats_app
    screen = application.window.user_screen
    camera.place_person(1.0)
    wait_until(qt_app, lambda: application.vision.engaged)

    application.conversation.ask(application.settings.faq[0].question, "shortcut")
    wait_until(qt_app, lambda: len(screen.transcript_texts()) == 3)
    application.conversation.ask("คำถามที่ไม่มีใครตอบได้")
    wait_until(qt_app, lambda: len(screen.transcript_texts()) == 5)
    screen.feedback_given.emit(False)

    camera.clear()
    wait_until(qt_app, lambda: not application.vision.present)

    summary = application.analytics.summary(1)
    assert (summary.questions, summary.answered, summary.feedback_down) == (2, 1, 1)
    assert summary.by_mode == {"shortcut": 1, "text": 1}
    assert (summary.visits, summary.engaged_visits) == (1, 1)
    waiting = application.analytics.needing_attention(1)
    assert [item.question for item in waiting] == ["คำถามที่ไม่มีใครตอบได้"]
    with application.database.connect() as connection:
        visit = connection.execute("SELECT questions, engaged, closest_m FROM visits").fetchone()
    assert (visit["questions"], visit["engaged"]) == (2, 1) and visit["closest_m"] == pytest.approx(1.0)


def test_stats_tab_shows_the_numbers_and_turns_a_missed_question_into_knowledge(qt_app, stats_app):
    application, _camera = stats_app
    application.conversation.ask("ค่าเทอมเท่าไหร่")
    wait_until(qt_app, lambda: state(application) is ConversationState.SPEAKING)

    tab = application.window.admin_screen._stats_tab
    tab.refresh()
    assert tab._tiles["questions"][0].text() == "1"
    assert tab._tiles["answered"][0].text() == "0%"
    assert tab._tiles["satisfaction"][0].text() == "–"  # nobody has rated yet: no made-up percentage
    assert tab._table.rowCount() == 1 and tab._table.item(0, 1).text() == "ค่าเทอมเท่าไหร่"
    assert sum(tab._chart._values) == 1
    tab._chart.resize(700, 220)
    tab._chart.grab()  # painting with data must not raise

    missed = application.analytics.needing_attention(1)[0]
    application.content.save_knowledge(None, "FAQ", missed.question, "ค่าเทอม 3,000 บาทต่อภาคเรียนครับ", "ค่าเทอม", True, False)
    application.analytics.resolve(missed.id)
    tab.refresh()
    assert tab._table.rowCount() == 0

    application.conversation.ask("ค่าเทอมเท่าไหร่")  # the robot has learned it
    wait_until(qt_app, lambda: application.analytics.summary(1).answered == 1)


def test_scheduled_announcement_runs_once_when_the_robot_is_free(qt_app, stats_app):
    application, _camera = stats_app
    application.content.save_schedule(None, "เชิญชวนเช้า", "12:00", "0123456", "ขอเชิญชมนิทรรศการที่หอประชุมครับ", None, True)
    clock = Clock(NOON - timedelta(minutes=1))
    application.schedule._clock = clock
    screen = application.window.user_screen
    screen.show_page(UserScreen.PAGE_CHAT)

    application.schedule.check()
    assert state(application) is ConversationState.IDLE  # not yet time

    clock.now = NOON + timedelta(seconds=20)
    application.schedule.check()
    assert state(application) is ConversationState.SPEAKING
    assert screen.current_page() == UserScreen.PAGE_CHAT  # announced on the page that is open
    assert screen._message_bar.text() == "ขอเชิญชมนิทรรศการที่หอประชุมครับ"
    assert application.content.schedules()[0].last_run_date == "2026-10-12"

    application.conversation.stop()
    application.schedule.check()  # still inside the grace period, but already done today
    assert state(application) is ConversationState.IDLE
    actions = [e.action for e in application.content.recent_activity() if e.actor == "schedule"]
    assert actions == ["schedule.run"]


def test_scheduled_announcement_waits_while_someone_is_talking_to_the_robot(qt_app, stats_app):
    application, _camera = stats_app
    application.content.save_schedule(None, "ประกาศ", "12:00", "0123456", "ข้อความประกาศ", None, True)
    application.schedule._clock = Clock(NOON)
    screen = application.window.user_screen

    application.conversation.ask(application.settings.faq[0].question)
    wait_until(qt_app, lambda: state(application) is ConversationState.SPEAKING)
    application.schedule.check()
    assert screen.current_page() == UserScreen.PAGE_CHAT  # the visitor's answer is not cut off
    assert application.content.schedules()[0].last_run_date == ""

    application.window.enter_admin()
    application.schedule.check()
    assert application.window.in_admin_mode() and application.content.schedules()[0].last_run_date == ""
    application.window.leave_admin()

    application.schedule.check()  # free now: delivered
    assert application.content.schedules()[0].last_run_date == "2026-10-12"


def test_admin_can_trigger_an_announcement_now_and_input_is_validated(qt_app, stats_app):
    application, _camera = stats_app
    content = application.content
    for bad in (("", "08:00", "0", "ข้อความ"), ("ชื่อ", "25:00", "0", "ข้อความ"),
                ("ชื่อ", "08:00", "", "ข้อความ"), ("ชื่อ", "08:00", "0", "")):
        with pytest.raises(ContentError):
            content.save_schedule(None, bad[0], bad[1], bad[2], bad[3], None, True)
    content.save_schedule(None, "ทดสอบ", "7.05", "01234", "ทดสอบประกาศ", None, True)
    saved = content.schedules()[0]
    assert (saved.time_of_day, saved.weekdays) == ("07:05", "01234")

    application.window.enter_admin()
    application.window.admin_screen.run_schedule_requested.emit(saved)
    assert not application.window.in_admin_mode()
    assert state(application) is ConversationState.SPEAKING
    assert content.schedules()[0].last_run_date == ""  # a manual run does not use up today's slot


def test_schedule_tab_lists_schedules_and_asks_for_an_immediate_run(qt_app, stats_app):
    from ui.admin_schedule import ScheduleTab

    application, _camera = stats_app
    tab = ScheduleTab(application.content)
    requested = []
    tab.run_requested.connect(requested.append)
    tab._run_now()
    assert requested == []  # nothing selected

    application.content.save_schedule(None, "เช้า", "08:30", "01234", "สวัสดีตอนเช้าครับ", None, True)
    assert tab._table.rowCount() == 1  # the tab follows content changes
    cells = [tab._table.item(0, column).text() for column in range(tab._table.columnCount())]
    assert cells[:4] == ["08:30", "จันทร์–ศุกร์", "เช้า", "สวัสดีตอนเช้าครับ"] and cells[5] == "ยังไม่เคย"
    tab._table.selectRow(0)
    tab._run_now()
    assert [item.title for item in requested] == ["เช้า"]
    tab.deleteLater()


def test_ai_tab_shows_what_would_be_sent_for_a_question(qt_app, stats_app):
    application, _camera = stats_app
    service = application.conversation_service
    preview = service.preview("อยากสมัครเรียนต้องไปที่ไหน")
    assert preview.exact_match is None
    assert preview.ranked[0][0].question == application.settings.faq[2].question
    assert preview.knowledge_size == len(application.settings.faq) == len(preview.sent_items)
    assert application.settings.faq[2].answer in preview.instruction
    assert service.preview(application.settings.faq[0].question).exact_match is not None

    from ui.admin_ai import AiTab

    tab = AiTab(service)
    assert tab._preview() is False  # nothing typed yet
    tab._question.setText("อยากสมัครเรียนต้องไปที่ไหน")
    assert tab._preview() is True
    assert tab._ranked.rowCount() >= 1 and tab._ranked.item(0, 2).text() == "ใช่"
    assert "<ข้อมูลสถานที่>" in tab._instruction.toPlainText()

    before = application.analytics.summary(1).questions
    tab._ask()
    wait_until(qt_app, lambda: tab._answer.text().startswith(application.settings.faq[2].answer))
    assert application.analytics.summary(1).questions == before  # an Admin test is not a visitor's question
    tab.deleteLater()
