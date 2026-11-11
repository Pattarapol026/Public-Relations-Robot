"""The places the robot does public relations for.

Two are offered: the invention competition it is entered in, and a turtle
conservation centre. The visitor picks one with the buttons at the top of the
robot's screen (the Admin can too, Admin → ฐานความรู้). Every knowledge-base
entry belongs to one place, or to both (``SHARED``: greetings, the robot itself,
emergencies); only the entries of the place in use, and the shared ones, are
used to answer.

To offer another place, add a ``Venue`` to ``VENUES`` (the id is stored in the
database, so never change an id once used). Entries of places taken out of the
list (hospital, hotel, college, ... before 2026-11-05) stay in the database but
are never used or shown.

Each place also has ``cues``: words that make a question sound like it is about
that place. ``services/venue_router.py`` uses them (with the place's own
knowledge) to answer a question about the other place from that place's data,
when the Admin allows it.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

SHARED = ""  # an entry used whichever place is chosen


@dataclass(frozen=True)
class Venue:
    id: str
    label: str  # shown in Admin
    short: str  # on the robot's screen, where the visitor picks the place
    place: str  # how the robot refers to the place ("ศูนย์อนุรักษ์แห่งนี้")
    focus: str  # what visitors here usually ask about; guides the model's answers
    general_chat: bool = False  # may also answer general-knowledge questions
    # What the robot says first when a visitor arrives, or when this place is picked on
    # screen (the Admin's own greeting in Admin → บุคลิก takes precedence). "" = the general one.
    # Kept short: the microphone opens only after the greeting has had its reading pause (60 ms a character).
    greeting: str = ""  # a template: {who} is "ผมชื่อไอร่า เป็นหุ่นยนต์ประชาสัมพันธ์" (or without the name)
    greeting_en: str = ""  # the same in English: {who} is "I'm Aira, the reception robot"
    # Words that point to this place. Thai cues are found anywhere in the text,
    # so each must not hide inside common words; English cues count as whole words only.
    cues: tuple[str, ...] = ()

    def greeting_for(self, who: str, english: bool = False) -> str:
        """The greeting with the robot's introduction of itself filled in ("" if the place has none)."""
        template = self.greeting_en if english else self.greeting
        return template.format(who=who) if template else ""


INVENTION = "invention"
TURTLE = "turtle"

VENUES: tuple[Venue, ...] = (
    Venue(INVENTION, "งานประกวดสิ่งประดิษฐ์ / การแข่งขัน", "สิ่งประดิษฐ์", "งานประกวดสิ่งประดิษฐ์ครั้งนี้",
          "ผลงานสิ่งประดิษฐ์ที่ส่งประกวด ทีมผู้พัฒนา ประเภทการแข่งขัน เกณฑ์การตัดสิน กำหนดการ "
          "รางวัล บูธจัดแสดง และวิธีการทำงานของผลงาน",
          greeting="สวัสดีครับ ยินดีต้อนรับ {who}ข้อมูลสิ่งประดิษฐ์ ถามได้เลยครับ",
          greeting_en="Hello, and welcome! {who} for the invention contest. Feel free to ask me anything.",
          cues=(
              "สิ่งประดิษฐ์", "ประดิษฐ์", "นวัตกรรม", "ผลงาน", "การแข่งขัน", "แข่งขัน", "ประกวด",
              "ส่งประกวด", "ผู้เข้าแข่งขัน", "ทีมผู้พัฒนา", "ทีมพัฒนา", "ผู้พัฒนา", "ผู้ประดิษฐ์",
              "คณะกรรมการ", "กรรมการตัดสิน", "เกณฑ์การตัดสิน", "ให้คะแนน", "คะแนน", "รางวัล",
              "ชนะเลิศ", "รองชนะเลิศ", "เหรียญทอง", "บูธ", "ต้นแบบ", "จดสิทธิบัตร", "สิทธิบัตร",
              "อนุสิทธิบัตร", "ใช้งานยังไง", "ทำงานยังไง", "ทำงานอย่างไร", "ทำมาจาก", "ต้นทุน",
              "สร้างขึ้น", "พัฒนาขึ้น", "หุ่นยนต์ตัวนี้", "หุ่นยนต์นี้",
              "invention", "inventions", "inventor", "innovation", "competition", "contest",
              "contestant", "judge", "judges", "judging", "award", "awards", "prize", "booth",
              "prototype", "patent", "entry", "entries", "team",
          )),
    Venue(TURTLE, "ศูนย์อนุรักษ์พันธุ์เต่า", "ศูนย์อนุรักษ์พันธุ์เต่า", "ศูนย์อนุรักษ์พันธุ์เต่าแห่งนี้",
          "พันธุ์เต่าที่ดูแล การอนุบาลและปล่อยเต่า การวางไข่ การอนุรักษ์ จุดชมและบ่อเต่า "
          "เวลาเปิดปิด ค่าเข้าชม กติกาการเข้าชม และการมีส่วนร่วมช่วยอนุรักษ์",
          greeting="สวัสดีครับ ยินดีต้อนรับ {who}ข้อมูลศูนย์เต่า ถามได้เลยครับ",
          greeting_en="Hello, and welcome! {who} for the turtle conservation centre. Feel free to ask me anything.",
          cues=(
              "เต่า", "เต่าทะเล", "เต่ากระ", "เต่าตนุ", "เต่ามะเฟือง", "เต่าหญ้า", "เต่าหัวฆ้อน",
              "ลูกเต่า", "ไข่เต่า", "อนุบาลเต่า", "บ่ออนุบาล", "บ่อเต่า", "ปล่อยเต่า", "อาหารเต่า",
              "ให้อาหาร", "วางไข่", "ฟักไข่", "อนุรักษ์", "ศูนย์อนุรักษ์", "สัตว์ทะเล", "ทะเล",
              "ชายหาด", "ขยะทะเล", "พลาสติก", "ใกล้สูญพันธุ์", "สูญพันธุ์", "เข้าชม", "ค่าเข้าชม",
              "turtle", "turtles", "sea turtle", "hatchling", "hatchlings", "nesting", "conservation",
              "endangered", "marine", "release", "eggs",
          )),
)
DEFAULT_VENUE = INVENTION

_BY_ID = {venue.id: venue for venue in VENUES}


def get(venue_id: str) -> Venue:
    """The venue with this id; the default one if the id is unknown (e.g. removed)."""
    return _BY_ID.get(venue_id, _BY_ID[DEFAULT_VENUE])


def is_known(venue_id: str) -> bool:
    return venue_id in _BY_ID


def label(venue_id: str) -> str:
    """Admin's name for an entry's venue, including the shared one."""
    return "ใช้ร่วมทุกหมวด" if venue_id == SHARED else get(venue_id).label


def cues_in(text: str, venue: Venue) -> list[str]:
    """The venue's cue words found in ``text`` (a longer cue hides the shorter ones inside it)."""
    lower = text.lower()
    found: list[str] = []
    for cue in sorted(venue.cues, key=len, reverse=True):
        if cue.isascii():
            phrase = r"[\s-]*".join(re.escape(part) for part in cue.replace("-", " ").split())
            hit = re.search(rf"(?<![a-z0-9]){phrase}(?![a-z0-9])", lower) is not None
        else:
            hit = cue in lower and not any(cue in longer for longer in found)
        if hit:
            found.append(cue)
    return found


def from_text(text: str) -> str | None:
    """A venue id from what a CSV cell holds (an id or a label); None if not recognised."""
    text = text.strip()
    if text in ("ใช้ร่วมทุกหมวด", "ทุกหมวด", "shared", "all"):
        return SHARED
    lowered = text.lower()
    for venue in VENUES:
        if lowered == venue.id or text in (venue.label, venue.short) or text in venue.label.split(" / "):
            return venue.id
    return None
