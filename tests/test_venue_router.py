"""A question about another kind of place is answered from that place's data, and only then."""
from __future__ import annotations

import pytest
from PySide6.QtWidgets import QApplication

from app.application import Application
from database.models import KnowledgeItem
from services import venue_router, venues
from services.conversation_service import ConversationService, Exchange
from ui.fonts import apply_app_font
from ui.log_bridge import QtLogHandler


def entry(n: int, venue: str, question: str, answer: str, keywords: tuple[str, ...] = ()) -> KnowledgeItem:
    return KnowledgeItem(n, "", question, answer, keywords, True, False, "", venue)


INVENTION, TURTLE = venues.INVENTION, venues.TURTLE
OWN = {
    INVENTION: [
        entry(1, INVENTION, "ห้องน้ำอยู่ที่ไหน", "ข้างบูธ 3 ครับ", ("ห้องน้ำ",)),
        entry(2, INVENTION, "ค่าเข้างานเท่าไหร่", "เข้าชมงานฟรีครับ", ("ค่าเข้างาน",)),
        entry(5, INVENTION, "ผลงานสิ่งประดิษฐ์นี้ทำงานอย่างไร", "ใช้กล้องและไมโครโฟนตอบคำถามผู้มาติดต่อ",
              ("สิ่งประดิษฐ์", "ทำงาน")),
    ],
    TURTLE: [
        entry(3, TURTLE, "ห้องน้ำอยู่ที่ไหน", "ข้างบ่ออนุบาลครับ", ("ห้องน้ำ",)),
        entry(4, TURTLE, "ปล่อยลูกเต่าลงทะเลกี่โมง", "ทุกวันเวลา 16.00 น. ที่ชายหาดหน้าศูนย์", ("ปล่อยเต่า", "ลูกเต่า")),
    ],
}


@pytest.mark.parametrize("text, venue_id, expected", [
    ("ผลงานสิ่งประดิษฐ์นี้ทำงานยังไง", INVENTION, ["สิ่งประดิษฐ์", "ทำงานยังไง", "ผลงาน"]),
    ("ปล่อยลูกเต่าลงทะเลกี่โมง", TURTLE, ["ลูกเต่า", "ทะเล"]),  # "เต่า" is inside "ลูกเต่า": once
    ("Can I see the sea turtles?", TURTLE, ["turtles"]),  # whole words: "sea turtle" is not "sea turtles"
    ("Is there a teammate here?", INVENTION, []),  # English cues are whole words ("team")
])
def test_cue_words(text, venue_id, expected):
    assert sorted(venues.cues_in(text, venues.get(venue_id))) == sorted(expected)


def test_both_places_have_cue_words():
    assert all(venue.cues for venue in venues.VENUES)
    assert venues.from_text("งานประกวดสิ่งประดิษฐ์") == INVENTION  # its CSV name
    assert venues.from_text("ศูนย์อนุรักษ์พันธุ์เต่า") == TURTLE


def test_a_question_clearly_about_the_other_place_goes_to_that_place():
    route = venue_router.route("ผลงานสิ่งประดิษฐ์นี้ทำงานยังไง", TURTLE, OWN)
    assert route.venue_id == INVENTION and route.switched
    route = venue_router.route("ปล่อยลูกเต่าลงทะเลกี่โมงครับ", INVENTION, OWN)
    assert route.venue_id == TURTLE
    assert "turtle(" in route.summary() and "ลูกเต่า" not in route.summary()  # never the question


def test_what_both_places_have_stays_with_the_place_in_use():
    assert venue_router.route("ห้องน้ำอยู่ไหน", INVENTION, OWN).venue_id == INVENTION
    assert venue_router.route("ห้องน้ำอยู่ไหน", TURTLE, OWN).venue_id == TURTLE
    # Matching the other place's entry is not enough without a word pointing to that place.
    only_turtle = {TURTLE: OWN[TURTLE]}
    assert venue_router.route("ห้องน้ำอยู่ไหน", INVENTION, only_turtle).venue_id == INVENTION


def test_one_passing_word_is_not_enough():
    assert venue_router.route("ทะเลสวยจัง", INVENTION, OWN).venue_id == INVENTION


def test_a_place_with_no_entries_is_never_chosen():
    no_turtle = {INVENTION: OWN[INVENTION]}
    assert venue_router.route("ปล่อยลูกเต่าลงทะเลกี่โมง", INVENTION, no_turtle).venue_id == INVENTION


def test_places_no_longer_offered_are_never_chosen():
    old = {**OWN, "hospital": [entry(9, "hospital", "ผู้ป่วยใหม่ทำบัตรที่ไหน", "ชั้น 1", ("ผู้ป่วยใหม่",))]}
    route = venue_router.route("ผู้ป่วยใหม่ทำบัตรที่ไหน", INVENTION, old)
    assert route.venue_id == INVENTION and all(s.venue_id != "hospital" for s in route.scores)


# --- in the application ---


@pytest.fixture(scope="module")
def qt_app():
    qt_app = QApplication.instance() or QApplication([])
    apply_app_font(qt_app)
    return qt_app


@pytest.fixture
def application(qt_app, settings):
    application = Application(settings, QtLogHandler())
    yield application
    application.shutdown()
    application.window.deleteLater()


class FakeGemini:
    model = "fake"

    def __init__(self):
        self.calls = []

    def generate(self, system_instruction, turns, max_output_tokens=400, temperature=0.3):
        self.calls.append(system_instruction)
        return "ok"


def add_contest_entry(content):
    content.save_knowledge(None, "ผลงาน", "ผลงานสิ่งประดิษฐ์นี้ทำงานอย่างไร",
                           "ใช้กล้องและไมโครโฟนตอบคำถามผู้มาติดต่อ", "สิ่งประดิษฐ์, ทำงาน",
                           True, False, venue=venues.INVENTION)


def test_the_turtle_centre_robot_answers_a_contest_question_from_the_contest_data(application):
    content = application.content
    add_contest_entry(content)
    content.set_venue(TURTLE)
    service = application.conversation_service
    assert content.venue_auto()  # on by default

    answer = service.respond("ผลงานสิ่งประดิษฐ์นี้ทำงานยังไงครับ")
    assert answer.answer_text == "ใช้กล้องและไมโครโฟนตอบคำถามผู้มาติดต่อ"
    # A follow-up stays with the place the conversation is about.
    history = (Exchange("ผลงานสิ่งประดิษฐ์นี้ทำงานยังไงครับ", answer.answer_text, "th"),)
    assert content.knowledge_for("ผลงานสิ่งประดิษฐ์นี้ทำงานยังไงครับ แล้วใช้ไฟอะไร").place.venue.id == venues.INVENTION
    assert service.respond("แล้วใช้ไฟอะไร", history).source == "fallback"  # nothing on power yet

    content.set_venue_auto(False)  # Admin: only the place in use
    assert service.respond("ผลงานสิ่งประดิษฐ์นี้ทำงานยังไงครับ").source == "fallback"
    assert any(e.action == "venue.auto" for e in content.recent_activity())


def test_the_model_speaks_for_where_it_stands_with_the_other_places_data(application):
    content = application.content
    add_contest_entry(content)
    content.set_venue(TURTLE)
    content.save_announcement(None, "งดให้อาหารเต่าวันนี้", "", True, venue=TURTLE)
    gemini = FakeGemini()
    application.conversation_service.set_gemini(gemini)

    # Not answered by the entry itself (that would come straight from the knowledge base).
    application.conversation_service.respond("ผลงานสิ่งประดิษฐ์นี้ใช้ไฟกี่วัตต์")
    instruction = gemini.calls[-1]
    assert "หุ่นยนต์ประชาสัมพันธ์ของศูนย์อนุรักษ์พันธุ์เต่าแห่งนี้" in instruction
    assert "คำถามนี้น่าจะเกี่ยวกับงานประกวดสิ่งประดิษฐ์ครั้งนี้" in instruction
    assert "ใช้กล้องและไมโครโฟน" in instruction
    assert "งดให้อาหารเต่า" not in instruction  # one place's data at a time, announcements too

    application.conversation_service.respond("วันนี้ให้อาหารเต่าได้ไหม")
    assert "งดให้อาหารเต่า" in gemini.calls[-1] and "คำถามนี้น่าจะเกี่ยวกับ" not in gemini.calls[-1]


TUITION = [
    entry(1, INVENTION, "ห้องน้ำอยู่ที่ไหน", "ข้างอาคาร 1 ครับ", ("ห้องน้ำ",)),
    entry(2, INVENTION, "ค่าเทอมเท่าไหร่", "ภาคเรียนละ 2,000 บาท", ("ค่าเทอม",)),
]


def test_a_clear_match_is_answered_at_once_and_the_model_is_asked_only_otherwise():
    items = TUITION
    gemini = FakeGemini()
    service = ConversationService(lambda: items, gemini=gemini, knowledge_first=True)
    answer = service.respond("ค่าเทอมเท่าไหร่ครับ")
    assert (answer.answer_text, answer.source) == ("ภาคเรียนละ 2,000 บาท", "knowledge") and gemini.calls == []
    service.respond("มีหอพักไหม")
    assert len(gemini.calls) == 1  # no entry for it: the model is asked
    service.respond("ค่าเทอมจ่ายผ่านแอปได้ไหม")
    assert len(gemini.calls) == 2  # near an entry, but not answered by it
    gemini.calls.clear()
    # A follow-up leans on the conversation, so the model answers it.
    service.respond("แล้วค่าเทอมล่ะ", (Exchange("มีหอพักไหม", "ok", "th"),))
    assert len(gemini.calls) == 1
    # Off: the model is asked first, as before (not the entry's exact words: those never need it).
    ConversationService(lambda: items, gemini=gemini).respond("อยากรู้ว่าค่าเทอมเท่าไหร่")
    assert len(gemini.calls) == 2


def test_without_a_router_the_service_behaves_as_before():
    items = TUITION
    service = ConversationService(lambda: items)
    assert service.respond("ค่าเทอมเท่าไหร่").answer_text == "ภาคเรียนละ 2,000 บาท"
