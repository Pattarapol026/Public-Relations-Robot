"""The event's pictures of 10 Nov 2026 (schedule, map, building 9, registration, group 5): the numbers add up and the
questions find their answers."""
from __future__ import annotations

import pytest

from ai import knowledge_service as ks
from database.models import KnowledgeItem
from tools.invention_event import CHONBURI, SAMUT_PRAKAN, TOTALS, event_entries


def items() -> list[KnowledgeItem]:
    return [
        KnowledgeItem(index, category, question, answer, tuple(k for k in keywords.split(",") if k), True, shortcut,
                      "", "invention", question_en, answer_en)
        for index, (category, question, answer, keywords, shortcut, question_en, answer_en) in enumerate(event_entries(), 1)
    ]


def answers() -> dict[str, str]:
    return {item.question: item.answer for item in items()}


def test_the_concept_paper_table_adds_up_the_way_the_picture_does():
    assert TOTALS == (23, 12, 18, 14, 7, 23, 2) and sum(TOTALS) == 99
    assert (sum(CHONBURI), sum(SAMUT_PRAKAN)) == (68, 31)


def test_every_entry_has_an_answer_in_both_languages_and_no_question_is_asked_twice():
    entries = event_entries()
    assert len({entry[1] for entry in entries}) == len(entries)
    assert all(entry[2] and entry[5] and entry[6] for entry in entries)


def test_the_schedule_says_what_the_pictures_say():
    said = answers()
    assert "13.00 ถึง 14.00 น. ที่อาคาร 9 ชั้น 1" in said["ลงทะเบียนผลงานและรายงานตัวที่ไหน เวลาไหน"]
    assert "ชั้น 3" in said["ลงทะเบียนและประชุมคณะกรรมการตัดสินเมื่อไหร่ ที่ไหน"]
    assert "ดร.พีระพงษ์ พันธ์โสดา" in said["พิธีปิดและประกาศผลเมื่อไหร่ ใครเป็นผู้ปิดงาน"]
    assert "16.30 ถึง 17.30" in said["พิธีปิดและประกาศผลเมื่อไหร่ ใครเป็นผู้ปิดงาน"]
    assert "ชั้น 4" in said["พิธีเปิดเริ่มกี่โมง และจัดที่ไหน"] and "09.00" in said["พิธีเปิดเริ่มกี่โมง และจัดที่ไหน"]
    assert "12.00 ถึง 13.00" in said["พักกลางวันกี่โมง"]
    assert "T-VET" in said["ผลงานประเภทที่ 4 แสดงที่ไหน"] and "T-VET" in said["ผลงานประเภทที่ 5 แสดงที่ไหน"]
    for number in (1, 3, 6, 7):
        assert "ลานอเนกประสงค์" in said[f"ผลงานประเภทที่ {number} แสดงที่ไหน"]
    assert "933" in said["ผลงานประเภทที่ 2 แสดงที่ไหน"]


def test_the_documents_to_bring_are_the_three_on_the_picture():
    said = answers()["วันประกวดต้องนำอะไรมาที่จุดลงทะเบียน"]
    assert "รูปเล่ม 2 เล่ม" in said and "1 แผ่น" in said and "A4" in said and "เฉพาะประเภทที่ 2" in said


def test_group_5_counts_per_category_come_from_the_table():
    said = answers()
    assert "99 ชิ้น" in said["กลุ่มจังหวัดที่ 5 มีผลงานทั้งหมดกี่ชิ้น"]
    assert "มี 2 ชิ้นครับ ชลบุรี 2 ชิ้น สมุทรปราการไม่มี" in said["ประเภทที่ 7 ในกลุ่มจังหวัดที่ 5 มีผลงานกี่ชิ้น"]
    assert "มี 23 ชิ้นครับ ชลบุรี 16 ชิ้น สมุทรปราการ 7 ชิ้น" in said["ประเภทที่ 1 ในกลุ่มจังหวัดที่ 5 มีผลงานกี่ชิ้น"]
    assert "68 ชิ้น" in said["สอจ.ชลบุรีมีผลงานในกลุ่มจังหวัดที่ 5 กี่ชิ้น"]
    assert "31 ชิ้น" in said["สอจ.สมุทรปราการมีผลงานในกลุ่มจังหวัดที่ 5 กี่ชิ้น"]
    assert "63" in said["ตัวเลขชลบุรี 63 กับ 68 ต่างกันทำไม"] and "68" in said["ตัวเลขชลบุรี 63 กับ 68 ต่างกันทำไม"]


@pytest.mark.parametrize("question, expected", [
    ("เริ่มกี่โมงครับ", "งานเริ่มกี่โมง"),
    ("ใครเป็นคนปิดงาน", "พิธีปิดและประกาศผลเมื่อไหร่ ใครเป็นผู้ปิดงาน"),
    ("ห้องน้ำอยู่ไหน", "ห้องน้ำอยู่ที่ไหน"),
    ("where is the toilet", "ห้องน้ำอยู่ที่ไหน"),
    ("จอดรถที่ไหน", "ที่จอดรถอยู่ที่ไหน"),
    ("รูปเล่มต้องมากี่เล่ม", "รูปเล่มที่ต้องนำมาลงทะเบียนมีกี่เล่ม"),
    ("ประเภทที่ 4 แสดงที่ไหน", "ผลงานประเภทที่ 4 แสดงที่ไหน"),
    ("ห้อง 933 คืออะไร", "ห้อง 933 คือห้องอะไร"),
    ("ชั้น 4 มีอะไร", "อาคาร 9 ชั้น 4 มีอะไร"),
    ("กลุ่มจังหวัดที่ 5 มีผลงานกี่ชิ้น", "กลุ่มจังหวัดที่ 5 มีผลงานทั้งหมดกี่ชิ้น"),
    ("ประเภทที่ 7 มีกี่ชิ้นในกลุ่มจังหวัดที่ 5", "ประเภทที่ 7 ในกลุ่มจังหวัดที่ 5 มีผลงานกี่ชิ้น"),
    ("ประเภทไหนมีผลงานมากที่สุด", "ประเภทไหนมีผลงานมากที่สุดในกลุ่มจังหวัดที่ 5"),
])
def test_a_question_finds_the_entry_that_answers_it(question, expected):
    match = ks.confident_match(question, items())
    assert match is not None and match.question == expected
