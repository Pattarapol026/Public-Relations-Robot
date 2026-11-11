"""The two places (the invention competition and the turtle conservation centre): each has
its own knowledge, the visitor or the Admin switches between them, and one place's facts
never reach the other's answers."""
from __future__ import annotations

from dataclasses import replace

import sqlite3
from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication

from ai import knowledge_service
from app.application import Application
from app.content_controller import ContentError
from database import db as db_module
from database.db import Database
from database.models import KnowledgeItem
from services import knowledge_transfer, venues
from services.conversation_service import ConversationService
from ui.fonts import apply_app_font
from ui.log_bridge import QtLogHandler

DOCS = Path(__file__).resolve().parent.parent / "docs"


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
        return "ได้ครับ"


def test_only_the_two_places_are_offered():
    assert [venue.id for venue in venues.VENUES] == [venues.INVENTION, venues.TURTLE]
    assert venues.DEFAULT_VENUE == venues.INVENTION
    assert venues.get("hospital").id == venues.INVENTION  # a place no longer offered
    assert not venues.is_known("education")
    assert venues.from_text("ศูนย์อนุรักษ์พันธุ์เต่า") == venues.TURTLE
    assert venues.from_text("โรงพยาบาล / คลินิก") is None


def test_entries_from_before_kinds_of_place_belong_to_the_college(tmp_path):
    """The database migration is unchanged: old entries are marked as the college's."""
    path = tmp_path / "old.db"
    connection = sqlite3.connect(path)
    connection.executescript(
        db_module._SCHEMA + db_module._MIGRATIONS[1] + db_module._MIGRATIONS[2] + db_module._MIGRATIONS[3]
    )
    connection.execute("PRAGMA user_version = 4")
    connection.execute(
        "INSERT INTO knowledge_items (question, answer, created_at, updated_at) VALUES ('ถาม', 'ตอบ', 'a', 'b')"
    )
    connection.commit()
    connection.close()

    database = Database(path)
    database.initialize()
    with database.connect() as upgraded:
        assert upgraded.execute("SELECT question, answer, venue FROM knowledge_items").fetchone()[:] == (
            "ถาม", "ตอบ", "education",
        )


def test_switching_the_place_changes_what_the_robot_answers_from(application):
    content = application.content
    content.set_venue_auto(False)  # only the place in use, to see the switch alone
    assert content.venue_id() == venues.INVENTION
    content.save_knowledge(None, "", "เปิดกี่โมง", "เปิดตลอดงานครับ", "เปิด", True, False, venue=venues.INVENTION)
    content.save_knowledge(None, "", "เปิดกี่โมง", "เปิด 9 โมงเช้าครับ", "เปิด", True, False, venue=venues.TURTLE)
    content.save_knowledge(None, "", "คุณทำอะไรได้บ้าง", "ตอบคำถามได้ครับ", "", True, False, venue=venues.SHARED)
    service = application.conversation_service

    assert service.respond("เปิดกี่โมง").answer_text == "เปิดตลอดงานครับ"
    content.set_venue(venues.TURTLE)
    assert service.respond("เปิดกี่โมง").answer_text == "เปิด 9 โมงเช้าครับ"
    assert service.respond("คุณทำอะไรได้บ้าง").answer_text == "ตอบคำถามได้ครับ"  # shared: both places
    assert content.venue_id() == venues.TURTLE  # kept
    assert any(e.action == "venue.change" for e in content.recent_activity())
    with pytest.raises(ContentError):
        content.set_venue("hospital")


def test_entries_of_places_no_longer_offered_are_never_used_or_shown(application):
    content = application.content
    with application.database.connect() as connection:
        connection.execute(
            "INSERT INTO knowledge_items (question, answer, enabled, venue, created_at, updated_at) "
            "VALUES ('ห้องยาอยู่ไหน', 'ชั้น 1 ครับ', 1, 'hospital', 'a', 'b')"
        )
    with pytest.raises(ContentError):
        content.save_knowledge(None, "", "ถาม", "ตอบ", "", True, False, venue="hospital")
    assert application.conversation_service.respond("ห้องยาอยู่ไหน").answer_text != "ชั้น 1 ครับ"
    from ui.admin_content import KnowledgeTab

    (knowledge_tab,) = application.window.admin_screen.findChildren(KnowledgeTab)
    knowledge_tab.refresh()
    shown = {knowledge_tab._table.item(row, 1).text() for row in range(knowledge_tab._table.rowCount())}
    assert shown and "ห้องยาอยู่ไหน" not in shown  # still in the database, not in the Admin's table


def test_editing_an_entry_keeps_its_place_unless_changed(application):
    content = application.content
    content.save_knowledge(None, "", "ปล่อยเต่ากี่โมง", "16.00 น.", "", True, False, venue=venues.TURTLE)
    [item] = [i for i in content.knowledge() if i.question == "ปล่อยเต่ากี่โมง"]
    content.save_knowledge(item.id, "", "ปล่อยเต่ากี่โมง", "17.00 น.", "", True, False)  # Admin edits the answer
    [item] = [i for i in content.knowledge() if i.question == "ปล่อยเต่ากี่โมง"]
    assert (item.answer, item.venue) == ("17.00 น.", venues.TURTLE)
    content.delete_knowledge(item.id)
    assert not [i for i in content.knowledge() if i.question == "ปล่อยเต่ากี่โมง"]


def test_the_old_knowledge_file_brings_only_the_shared_entries(application):
    """docs/knowledge.csv still has the places no longer offered: those rows are refused."""
    content = application.content
    result = content.import_knowledge(DOCS / "knowledge.csv", keep_existing=True)
    assert result.added == 17 and len(result.errors) > 100  # 16 shared entries + "คุณชื่ออะไร"
    imported = [item for item in content.knowledge() if item.question_en]
    assert {item.venue for item in imported} == {venues.SHARED} and all(item.answer_en for item in imported)
    assert not any(knowledge_service.is_demo(item) for item in imported)


def test_the_invention_template_imports_into_the_contest_switched_off(application):
    result = application.content.import_knowledge(DOCS / "invention_template.csv", keep_existing=True)
    assert result.added == 5 and result.errors == []
    rows = [i for i in application.content.knowledge() if i.question.startswith("(ตัวอย่าง)")]
    assert {i.venue for i in rows} == {venues.INVENTION} and not any(i.enabled for i in rows)


@pytest.mark.parametrize("question, entry", [
    ("ใครสมัครได้บ้าง", "ใครส่งผลงานเข้าประกวดได้บ้าง"),
    ("ต้องลงทะเบียนก่อนกี่วัน", "ต้องลงทะเบียนล่วงหน้ากี่วัน"),
    ("ลืมลงทะเบียนทำไงดี", "ไม่ได้ลงทะเบียนแต่นำผลงานมาประกวดได้ไหม"),
    ("วสอศ 3 กี่หน้า", "แบบ ว-สอศ-3 ต้องมีกี่หน้า"),
    ("ประเภทที่ 1 คิดคะแนนยังไง", "เกณฑ์คะแนนประเภทที่ 1 เป็นอย่างไร"),
    ("ใช้ chatgpt ได้ไหม", "ใช้ ChatGPT หรือ API สำเร็จรูปได้ไหม"),
    ("ต้องมีเลข อย ไหม", "ผลงานอาหารต้องมีเลข อย. ไหม"),
    ("หุ่นยนต์ประเภท 7 ต้องเดินได้ไหม", "หุ่นยนต์ประเภทที่ 7 ต้องเคลื่อนที่ได้ไหม"),
    ("ประเภท 6 ต้องมีสปอนเซอร์กี่เปอร์เซ็นต์", "ประเภทที่ 6 ต้องมีผู้สนับสนุนงบประมาณเท่าไร"),
    ("ผลงานนี้ทำงานยังไง", "ผลงานชิ้นนี้ทำงานอย่างไร"),  # not a rule sharing "ผลงาน ... อย่างไร"
    ("Where do I register?", "ลงทะเบียนส่งผลงานได้ที่ไหน"),
    ("ห้องน้ำอยู่ไหน", "ห้องน้ำอยู่ที่ไหน"),  # from the campus map of 10 Nov 2026
    ("รหัสไวไฟคืออะไร", None),  # not in the rules or the pictures: left to the model
])
def test_the_contest_rules_answer_what_visitors_ask(application, question, entry):
    """docs/invention_knowledge.csv (from tools/build_invention_knowledge.py), as loaded into the robot."""
    path = DOCS / "invention_knowledge.csv"
    result = application.content.import_knowledge(path)
    assert result.added >= 110 and result.errors == []
    # Only the file's entries: a new database also seeds the college's sample questions into the contest.
    asked = {row.question for row in knowledge_transfer.read_csv(path)[0]}
    rows = [i for i in application.content.knowledge() if i.question in asked]
    assert {i.venue for i in rows} == {venues.INVENTION} and all(i.enabled for i in rows)
    found = knowledge_service.clear_match(question, rows) or knowledge_service.confident_match(question, rows)
    assert (found.question if found else None) == entry


def test_announcements_of_one_place_are_never_given_at_the_other(application):
    content = application.content
    content.set_venue_auto(False)
    content.save_announcement(None, "ประกาศผลรางวัล 15.00 น.", "", True, venue=venues.INVENTION)
    content.save_announcement(None, "งดให้อาหารเต่าวันนี้", "", True, venue=venues.TURTLE)
    gemini = FakeGemini()
    application.conversation_service.set_gemini(gemini)

    content.set_venue(venues.TURTLE)
    application.conversation_service.respond("วันนี้มีอะไรพิเศษไหม")
    assert "งดให้อาหารเต่าวันนี้" in gemini.calls[-1] and "ประกาศผลรางวัล" not in gemini.calls[-1]

    content.set_venue(venues.INVENTION)
    application.conversation_service.respond("วันนี้มีอะไรพิเศษไหม")
    assert "ประกาศผลรางวัล" in gemini.calls[-1] and "งดให้อาหารเต่า" not in gemini.calls[-1]


def test_a_csv_without_places_goes_to_the_place_in_use(application, tmp_path):
    content = application.content
    content.set_venue(venues.TURTLE)
    old = tmp_path / "old.csv"
    old.write_text("คำถาม,คำตอบ\nห้องน้ำอยู่ไหน,ข้างบ่ออนุบาลครับ\n", encoding="utf-8-sig")
    content.import_knowledge(old)
    [item] = [i for i in content.knowledge() if i.question == "ห้องน้ำอยู่ไหน"]
    assert item.venue == venues.TURTLE


def test_the_model_is_told_which_place_it_speaks_for_and_that_examples_are_examples():
    items = [KnowledgeItem(1, "DEMO", "ปล่อยเต่าที่ไหน", "(ข้อมูลตัวอย่าง) ชายหาดหน้าศูนย์", (), True, False, "",
                           venues.TURTLE)]
    gemini = FakeGemini()
    service = ConversationService(lambda: items, gemini=gemini,
                                  venue_provider=lambda: (venues.get(venues.TURTLE), ""))
    service.respond("จะไปดูปล่อยเต่าได้ที่ไหน")
    instruction = gemini.calls[-1]
    assert "หุ่นยนต์ประชาสัมพันธ์ของศูนย์อนุรักษ์พันธุ์เต่าแห่งนี้" in instruction
    assert f"{knowledge_service.DEMO_TAG} (ข้อมูลตัวอย่าง) ชายหาดหน้าศูนย์" in instruction
    assert "ต้องบอกผู้ฟังทุกครั้งว่าเป็นข้อมูลตัวอย่าง" in instruction
    # Greetings and small talk are answered, then the robot steers back to its place.
    assert "ทักทายหรือคุยทั่วไปสั้น ๆ ได้" in instruction
    assert "ให้ตอบจากความรู้ทั่วไปที่แน่ใจ" in instruction  # general knowledge: answered, not "no information"
    assert "ชวนต่อไปยังบริการของศูนย์อนุรักษ์พันธุ์เต่าแห่งนี้" in instruction
    assert "เช่น ห้องน้ำอยู่ไหน" in instruction  # but this place's own facts only from its data


ROBOT_INTRO = "ผมเป็นหุ่นยนต์ประชาสัมพันธ์"  # how a robot without a name introduces itself

def test_the_visitor_picks_the_place_on_the_robots_screen(application):
    screen = application.window.user_screen
    choice = screen._venue_choice  # a dropdown
    assert [choice.itemText(i) for i in range(choice.count())] == ["สิ่งประดิษฐ์", "ศูนย์อนุรักษ์พันธุ์เต่า"]
    assert choice.currentData() == venues.INVENTION
    turtle = choice.findData(venues.TURTLE)
    choice.setCurrentIndex(turtle)
    choice.activated.emit(turtle)  # what a visitor's pick sends
    assert application.content.venue_id() == venues.TURTLE
    assert choice.currentData() == venues.TURTLE
    application.content.set_venue(venues.INVENTION)  # the Admin switched it back: the screen follows
    assert choice.currentData() == venues.INVENTION


def test_picking_another_place_starts_a_new_chat_and_greets_as_that_places_robot(application):
    screen = application.window.user_screen
    choice = screen._venue_choice

    def pick(venue_id):
        index = choice.findData(venue_id)
        choice.setCurrentIndex(index)
        choice.activated.emit(index)

    screen._add_bubble("user", "คุณ: ลงทะเบียนยังไง")
    screen._add_bubble("robot", "หุ่นยนต์: ที่ Thaiinvention.net ครับ")
    assert len(screen.transcript_texts()) == 2

    pick(venues.TURTLE)
    greeting = venues.get(venues.TURTLE).greeting_for(ROBOT_INTRO)
    assert screen.transcript_texts() == [f"หุ่นยนต์: {greeting}"]  # the old chat is gone
    assert "ศูนย์เต่า" in greeting and "สิ่งประดิษฐ์" not in greeting

    pick(venues.INVENTION)
    greeting = venues.get(venues.INVENTION).greeting_for(ROBOT_INTRO)
    assert screen.transcript_texts() == [f"หุ่นยนต์: {greeting}"]
    assert "สิ่งประดิษฐ์" in greeting and "เต่า" not in greeting

    pick(venues.INVENTION)  # the same place again: nothing starts over
    screen._add_bubble("user", "คุณ: ขอบคุณ")
    pick(venues.INVENTION)
    assert len(screen.transcript_texts()) == 2


def test_each_place_greets_with_its_own_words_unless_the_admin_wrote_one(application):
    from datetime import datetime

    content = application.content
    noon = datetime(2026, 11, 11, 12, 0)
    content.set_venue(venues.TURTLE)
    assert content.greeting_at(noon) == venues.get(venues.TURTLE).greeting_for(ROBOT_INTRO)
    content.set_venue(venues.INVENTION)
    assert content.greeting_at(noon) == venues.get(venues.INVENTION).greeting_for(ROBOT_INTRO)
    persona = content.persona()
    content.save_persona(replace(persona, greeting_afternoon="สวัสดีตอนบายครับ"))
    assert content.greeting_at(noon) == "สวัสดีตอนบายครับ"  # the Admin's own words win



def test_the_language_button_changes_the_greeting_to_that_language(application):
    from datetime import datetime

    from PySide6.QtWidgets import QLabel

    content, screen = application.content, application.window.user_screen
    content.save_persona(replace(content.persona(), robot_name="Aira (ไอร่า)"))
    thai = content.greeting_at(datetime.now())
    english = content.greeting_at(datetime.now(), "en")
    assert "ผมชื่อไอร่า" in thai and "Aira" not in thai
    assert english.startswith("Hello") and "I'm Aira" in english and "invention contest" in english
    assert "ไอร่า" not in english

    screen.greet(thai)
    screen._toggle_language()  # Thai -> English
    assert screen.transcript_texts() == [f"หุ่นยนต์: {english}"]  # the greeting changed, it was not added to
    speakers = [w.text() for w in screen._bubbles.findChildren(QLabel) if w.property("role") == "speaker"]
    assert speakers == ["Aira"]
    screen._toggle_language()  # and back
    assert screen.transcript_texts() == [f"หุ่นยนต์: {thai}"]


def test_the_language_button_leaves_a_conversation_alone(application):
    screen = application.window.user_screen
    screen.greet(application.content.greeting_at(__import__("datetime").datetime.now()))
    screen._add_bubble("user", "คุณ: ลงทะเบียนยังไง")
    before = screen.transcript_texts()
    screen._toggle_language()
    assert screen.transcript_texts() == before  # only the language changes; nothing is cleared or repeated


def test_the_english_greeting_follows_the_place(application):
    from datetime import datetime

    content = application.content
    content.set_venue(venues.TURTLE)
    english = content.greeting_at(datetime.now(), "en")
    assert "turtle" in english and "invention" not in english
    content.set_venue(venues.INVENTION)
    assert "invention" in content.greeting_at(datetime.now(), "en")


def test_the_program_always_starts_in_the_invention_contest(qt_app, settings):
    first = Application(settings, QtLogHandler())
    first.content.set_venue(venues.TURTLE, "ศูนย์ทดสอบ")  # closed while the turtle centre was in use
    assert first.content.venue_id() == venues.TURTLE
    first.shutdown()
    first.window.deleteLater()

    second = Application(settings, QtLogHandler())  # the next start
    try:
        assert second.content.venue_id() == venues.INVENTION
        assert second.content.place_name(venues.TURTLE) == "ศูนย์ทดสอบ"  # names the Admin gave are kept
    finally:
        second.shutdown()
        second.window.deleteLater()


def test_the_knowledge_page_says_how_much_each_place_knows(application):
    from ui.admin_content import KnowledgeTab

    content = application.content
    page = KnowledgeTab(content)  # the same page the Admin shows under ฐานความรู้ > คำถาม-คำตอบ
    everything = content.knowledge()
    invention = sum(1 for item in everything if item.venue == venues.INVENTION)
    turtle = sum(1 for item in everything if item.venue == venues.TURTLE)
    text = page._venue_counts.text()
    assert f"{venues.get(venues.INVENTION).short} {invention} รายการ" in text
    assert f"{venues.get(venues.TURTLE).short} {turtle} รายการ" in text
    assert "ใช้ร่วมทุกหมวด" in text


def test_the_turtle_centre_sheet_builds_and_imports_into_the_turtle_place(application, tmp_path):
    from tools import build_turtle_knowledge as sheet

    sheet.check()  # every entry complete (Thai and English), no question twice
    assert len(sheet.ENTRIES) >= 200
    out = tmp_path / "turtle.csv"
    sheet.OUT = out
    sheet.main()
    report = application.content.import_knowledge(out)
    assert report.added == len(sheet.ENTRIES) and not report.errors
    turtle = [item for item in application.content.knowledge() if item.venue == venues.TURTLE]
    assert len(turtle) == len(sheet.ENTRIES) and all(item.enabled and item.answer_en for item in turtle)
    again = application.content.import_knowledge(out)
    assert again.added == 0 and again.updated == len(sheet.ENTRIES)  # re-importing never duplicates
