"""Which questions are about a place on the event's site plan (see ui/images/venue_map.png).

Plain rules on the visitor's words, no AI model. A question that names one of the numbered points or the buildings on the
plan ("ตึกอำนวยการอยู่ไหน", "where is building 9") brings the plan up on screen. Words that are only sometimes about
a place (registration, a category of invention) count only together with a word that asks where.
"""
from __future__ import annotations

import re

# Names that are always a place on the plan.
_PLACES = re.compile(
    r"ตึก\s*อำนวยการ|อาคาร\s*อำนวยการ|ตึกผู้อำนวยการ|ตึกอำ"
    r"|(?:อาคาร|ตึก)\s*(?:[1-3]|9|หนึ่ง|สอง|สาม|เก้า)(?!\d)"
    r"|ลาน\s*อเนกประสงค์|อเนกประสงค์"
    r"|หอประชุม|t-?\s*vet|ทีเว็ท|ทีเวท|ที\s*เวท"
    r"|ลานจอด|ที่จอดรถ|จอดรถ|สนามฟุตบอล"
    r"|ห้อง\s*(?:933|เก้าสามสาม)"
    r"|พื้นที่\s*(?:จัด)?แสดง|จุดแสดง"
    r"|แผนที่|แผนผัง|แพนผัง|แผ่นผัง|แพนที่|แผนพัง|ผังงาน|ผังบริเวณ|ผังสถานที่|จุดที่\s*[1-5]"
    r"|ซอยเทคนิค|เทคนิค\s*ซอย"
    r"|ห้องรับรอง|ห้องประชุมทองขาว|ห้องประมวลผล|ห้องประกัน"
    r"|building\s*(?:[1-3]|9|one|two|three|nine)\b|site\s*plan|\bmap\b|floor\s*plan|layout"
    r"|parking|car\s*park|football\s*field|multipurpose|auditorium|main\s*hall|director'?s?\s*building"
)
# Words that make "where" the point of a question about something that is not itself a place.
_WHERE = re.compile(
    r"ที่ไหน|ตรงไหน|ทางไหน|ไหน|ไปยังไง|ไปอย่างไร|เดินไปยังไง|อยู่|ไปทาง|ไปยัง|ไปที่|เดินไป|ทางไป|ลัด|จัดที่|จัดใน|จุดไหน|\bwhere\b|\bwhich way\b|how (?:do|can) i get"
)
# Things that are somewhere on the plan, but only a question that asks where is about the place.
_SOMETIMES = re.compile(
    r"ลงทะเบียน|สมัคร|รับประทานอาหาร|กินข้าว|ประเภท\s*(?:ที่\s*)?(?:[1-7]|หนึ่ง|สอง|สาม|สี่|ห้า|หก|เจ็ด)"
    r"|นิทรรศการ|ตัดสิน|ประกาศผล|พิธีเปิด|พิธีปิด|registration|register|exhibition|ceremony|judging"
    r"|ห้องน้ำ|ห้องสุขา|สุขา|ห้องส้วม|ห้องพยาบาล|พยาบาล|ประชาสัมพันธ์|เจ้าหน้าที่|ทางเข้า|ทางออก|ประตู|ที่นั่ง|ห้องประชุม|ห้องรับรอง"
    r"|โรงอาหาร|โรงเรียน|ร้านอาหาร|ร้านค้า|ร้านกาแฟ|ซื้ออาหาร|ซื้อน้ำ|น้ำดื่ม|ที่ชาร์จ|ชาร์จ|ตู้\s*atm|เอทีเอ็ม|ตึก|อาคาร|ลาน|หอ|ห้อง|บูธ|ผลงาน|ประเภท|ชั้น"
    r"|กรรมการ|ผู้อำนวยการ|ผอ|toilet|restroom|bathroom|wc|first aid|clinic|information|help desk|entrance|exit|gate|canteen|cafeteria|restaurant|shop|atm|building|room|booth|hall|floor"
)


# The robot's own answer points at the plan ("ดูตำแหน่งจากแผนผัง"): then the plan is shown whatever was asked.
_ANSWER_POINTS_AT_PLAN = re.compile(r"แผนผัง|แผนที่|ผังบริเวณ|site\s*plan|map|floor\s*plan|layout")


def wants_map(question: str, answer: str = "") -> bool:
    """True when the question is about where something is on the event's site (or the answer sends the visitor to the plan)."""
    text = " ".join(question.lower().split())
    if text:
        if _PLACES.search(text):
            return True
        if _SOMETIMES.search(text) and _WHERE.search(text):
            return True
    return bool(answer and _ANSWER_POINTS_AT_PLAN.search(answer.lower()))
