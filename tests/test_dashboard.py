"""Admin dashboard: top questions, recent warnings, and the cards that show them."""
from __future__ import annotations

import logging
from datetime import datetime

import pytest
from PySide6.QtWidgets import QApplication, QLabel

from app.application import Application
from database.repositories import InteractionRepository, VisitRepository
from services.analytics_service import AnalyticsService
from services.conversation_service import ConversationResult
from ui.fonts import BUNDLED_FAMILY, apply_app_font
from ui.log_bridge import MAX_PROBLEMS, QtLogHandler


@pytest.fixture(scope="module")
def qt_app():
    qt_app = QApplication.instance() or QApplication([])
    assert apply_app_font(qt_app) == BUNDLED_FAMILY
    return qt_app


def answer(source: str = "knowledge") -> ConversationResult:
    return ConversationResult("คำตอบ", "คำตอบ", source, "th")


def test_top_questions_merge_spacing_and_case_and_rank_by_count(database):
    analytics = AnalyticsService(
        InteractionRepository(database), VisitRepository(database),
        clock=lambda: datetime(2026, 10, 15, 10, 0),
    )
    for question in (
        "สมัครเรียนได้ที่ไหน", "ห้องน้ำอยู่ไหน", "สมัครเรียน ได้ที่ไหน", "Where is the library",
        "where is  the library", "สมัครเรียนได้ที่ไหน ",
    ):
        analytics.record_interaction(question, answer(), 1000, "voice")
    assert analytics.top_questions(7, limit=2) == [
        ("สมัครเรียนได้ที่ไหน", 3), ("where is the library", 2),
    ]


def test_top_questions_are_empty_when_question_text_is_not_kept(database):
    analytics = AnalyticsService(
        InteractionRepository(database), VisitRepository(database), log_questions=False
    )
    analytics.record_interaction("สมัครเรียนได้ที่ไหน", answer(), 1000, "voice")
    assert analytics.top_questions(7) == []


def test_log_handler_keeps_recent_warnings_newest_first(qt_app):
    handler = QtLogHandler()
    logger = logging.getLogger("test.dashboard")
    logger.addHandler(handler)
    seen = []
    handler.emitter.problem.connect(seen.append)
    try:
        logger.info("ordinary")  # not a problem
        logger.warning("first problem")
        try:
            raise ValueError("boom")
        except ValueError:
            logger.exception("second problem\nwith more lines")
        for index in range(MAX_PROBLEMS):
            logger.warning("filler %d", index)
    finally:
        logger.removeHandler(handler)
    problems = handler.recent_problems()
    assert len(problems) == MAX_PROBLEMS  # the oldest are dropped
    assert problems[0].message == f"filler {MAX_PROBLEMS - 1}"
    assert seen[1].level == "ERROR" and seen[1].message == "second problem"
    assert all(problem.source == "test.dashboard" for problem in problems)


def test_dashboard_shows_devices_today_top_questions_and_problems(qt_app, settings):
    handler = QtLogHandler()
    application = Application(settings, handler)
    try:
        for _ in range(2):
            application.analytics.record_interaction("ห้องสมุดอยู่ไหน", answer(), 1500, "voice")
        application.analytics.record_interaction("ค่าเทอมเท่าไร", answer("fallback"), 900, "text")
        logging.getLogger("test.dashboard").addHandler(handler)
        logging.getLogger("test.dashboard").error("Camera stopped")
        dashboard = application.window.admin_screen._dashboard
        dashboard.refresh()

        texts = {label.text() for label in dashboard.findChildren(QLabel)}
        assert "ห้องสมุดอยู่ไหน" in texts and "2 ครั้ง" in texts
        assert "Camera stopped" in texts
        assert dashboard._questions_card.value.text() == "3"
        assert dashboard._answered_card.value.text() == "67%"
        assert dashboard._robot_card.value.text() == "พร้อม"
        assert dashboard._camera_card.value.text() == "ปิดใช้งาน"  # VISION_ENABLED=false in tests
    finally:
        logging.getLogger("test.dashboard").removeHandler(handler)
        application.shutdown()
        application.window.deleteLater()
