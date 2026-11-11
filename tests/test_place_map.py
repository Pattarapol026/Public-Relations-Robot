"""Which questions bring up the site plan."""
import pytest

from services.place_map import wants_map


@pytest.mark.parametrize("question", [
    "ตึกอำนวยการอยู่ไหน", "อาคาร 9 อยู่ตรงไหนครับ", "ตึก 9 ไปทางไหน", "หอประชุม T-VET อยู่ที่ไหน", "ที่จอดรถอยู่ตรงไหน",
    "ห้อง 933 อยู่ชั้นไหน", "ขอดูแผนที่หน่อย", "ลงทะเบียนที่ไหน", "ประเภทที่ 4 จัดที่ไหน", "where is building 9",
    "Where can I park? parking", "Can I see the map?", "ลานอเนกประสงค์ มีอะไรบ้าง",
])
def test_a_question_about_a_place_shows_the_plan(question):
    assert wants_map(question)


@pytest.mark.parametrize("question", [
    "ค่าลงทะเบียนเท่าไหร่", "ประเภทที่ 4 คืออะไร", "งานจัดวันไหน", "สิ่งประดิษฐ์ที่เจ็ดคืออะไร", "สวัสดี", "", "ใครเป็นกรรมการ",
    "ภาคเหนือมีผลงานกี่ชิ้น",
])
def test_other_questions_do_not(question):
    assert not wants_map(question)


@pytest.mark.parametrize("question", [
    "ห้องน้ำไปทางไหน", "ห้องน้ำอยู่ที่ไหน", "ไหนแพนผัง", "ไหนแผนผัง", "ขอดูแผนผังหน่อย", "จุดประชาสัมพันธ์อยู่ตรงไหน",
    "ทางเข้าอยู่ไหน", "โรงอาหารไปทางไหน", "ห้องกรรมการอยู่ไหน", "บูธประเภทที่ 7 อยู่ที่ไหน", "ผลงานหุ่นยนต์อยู่ตรงไหน",
    "where is the toilet", "where is the entrance", "how do I get to the canteen", "ห้องพยาบาลไปยังไง", "ผอ.อยู่ที่ไหน",
])
def test_more_ways_of_asking_where_something_is(question):
    assert wants_map(question)


def test_an_answer_that_points_at_the_plan_brings_it_up():
    assert wants_map("เรื่องนั้นอยู่ไหนครับ", "ดูตำแหน่งจากแผนผังได้เลยครับ")
    assert not wants_map("สวัสดี", "สวัสดีครับ")
