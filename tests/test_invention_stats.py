"""The report of inventions by region and province (10 Nov 2026): the numbers add up and the questions find their answers."""
from __future__ import annotations

import pytest

from ai import knowledge_service as ks
from database.models import KnowledgeItem
from tools.invention_stats import CATEGORY, PENDING_REGIONS, REGION_TOTALS, REGIONS, TOTAL, stats_entries


def items() -> list[KnowledgeItem]:
    return [
        KnowledgeItem(index, category, question, answer, tuple(k for k in keywords.split(",") if k), True, shortcut,
                      "", "invention", question_en, answer_en)
        for index, (category, question, answer, keywords, shortcut, question_en, answer_en) in enumerate(stats_entries(), 1)
    ]


def test_the_numbers_in_the_report_add_up():
    assert sum(REGION_TOTALS.values()) == TOTAL == 316  # the second report (the first said 314, the North 17)
    for region, (_english, _alias, provinces) in REGIONS.items():
        if region not in PENDING_REGIONS:  # the North's province table was not sent again
            assert sum(count for _p, _e, count, _k in provinces) == REGION_TOTALS[region]


def test_every_province_has_its_own_entry_with_its_count_and_region():
    by_question = {item.question: item for item in items()}
    for region, (_english, _alias, provinces) in REGIONS.items():
        if region in PENDING_REGIONS:
            continue
        for province, _en, count, _keys in provinces:
            entry = by_question[f"จังหวัด{province}ส่งสิ่งประดิษฐ์เข้าประกวดกี่ชิ้น"]
            assert f"{count} ชิ้น" in entry.answer and region in entry.answer and entry.category == CATEGORY


@pytest.mark.parametrize("question, expected", [
    ("ชลบุรีส่งกี่ชิ้น", "จังหวัดชลบุรีส่งสิ่งประดิษฐ์เข้าประกวดกี่ชิ้น"),
    ("กรุงเทพมีสิ่งประดิษฐ์เท่าไหร่", "จังหวัดกรุงเทพมหานครส่งสิ่งประดิษฐ์เข้าประกวดกี่ชิ้น"),
    ("อยุธยามีกี่ผลงาน", "จังหวัดพระนครศรีอยุธยาส่งสิ่งประดิษฐ์เข้าประกวดกี่ชิ้น"),
    ("ภาคอีสานมีกี่ผลงาน", "ภาคตะวันออกเฉียงเหนือมีสิ่งประดิษฐ์กี่ชิ้น"),
    ("ภาคใต้มีจังหวัดอะไรบ้าง", "ภาคใต้มีจังหวัดอะไรบ้าง และแต่ละจังหวัดส่งกี่ชิ้น"),
    ("ปีนี้มีสิ่งประดิษฐ์กี่ชิ้น", "ปีนี้มีสิ่งประดิษฐ์กี่ชิ้น"),
    ("จังหวัดไหนส่งมากที่สุด", "จังหวัดไหนส่งสิ่งประดิษฐ์มากที่สุด"),
    ("ภาคไหนมีผลงานเยอะที่สุด", "ภาคไหนมีสิ่งประดิษฐ์มากที่สุด"),
])
def test_a_question_finds_the_entry_that_answers_it(question, expected):
    match = ks.confident_match(question, items())
    assert match is not None and match.question == expected


def test_the_total_and_the_leaders_are_what_the_report_says():
    by_question = {item.question: item.answer for item in items()}
    assert "316 ชิ้น" in by_question["ปีนี้มีสิ่งประดิษฐ์กี่ชิ้น"]
    assert by_question["จังหวัดไหนส่งสิ่งประดิษฐ์มากที่สุด"].startswith("ชลบุรี มากที่สุดครับ 63 ชิ้น")
    assert "ภาคตะวันออกและกรุงเทพมหานคร มากที่สุดครับ 219 ชิ้น" in by_question["ภาคไหนมีสิ่งประดิษฐ์มากที่สุด"]
    assert "10 จังหวัด" in by_question["จังหวัดไหนส่งสิ่งประดิษฐ์น้อยที่สุด"]


def test_what_depends_on_a_province_table_that_is_out_of_date_says_so_and_gives_no_old_numbers():
    by_question = {item.question: item.answer for item in items()}
    assert "19 ชิ้น" in by_question["ภาคเหนือมีสิ่งประดิษฐ์กี่ชิ้น"] and "จังหวัด" not in by_question["ภาคเหนือมีสิ่งประดิษฐ์กี่ชิ้น"].split("ครับ")[1]


@pytest.mark.parametrize("question", ["รวมทุกภาค ส่งเท่าไหร่", "ไม่ใช่ 316 หรอ", "รวมอะ ไม่ใช่ 316 หรอ", "ยอดรวมเท่าไหร่"])
def test_the_model_is_always_shown_the_total_for_a_question_about_it(question):
    """10 Nov 2026: asked "รวมทุกภาค ส่งเท่าไหร่" the model was not shown the total and added up provinces itself (315)."""
    shown = ks.select_for_context(question, items(), 12)
    assert "ปีนี้มีสิ่งประดิษฐ์กี่ชิ้น" in [item.question for item in shown]


def test_a_province_name_is_never_answered_with_another_places_number():
    """Where the knowledge answers by itself (no model), it is the right province or nothing at all."""
    for region, (_english, _alias, provinces) in REGIONS.items():
        for province, _en, _count, _keys in provinces:
            for question in (f"{province}ส่งกี่ชิ้น", f"จังหวัด{province}มีกี่ผลงาน", f"{province}มีผลงานกี่ชิ้น"):
                match = ks.clear_match(question, items())
                assert match is None or f"จังหวัด{province}" in match.question, (question, match and match.question)


def test_the_north_province_entries_say_the_figures_are_not_updated_instead_of_giving_old_numbers():
    by_question = {item.question: item.answer for item in items()}
    for province, _en, _count, _keys in REGIONS["ภาคเหนือ"][2]:
        answer = by_question[f"จังหวัด{province}ส่งสิ่งประดิษฐ์เข้าประกวดกี่ชิ้น"]
        assert "19 ชิ้น" in answer and "ยังไม่ได้อัปเดต" in answer
