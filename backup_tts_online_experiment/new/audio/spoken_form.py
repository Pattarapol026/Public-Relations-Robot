"""Written Thai as a person would say it: so that the robot talks, and does not read.

* A new line is NOT a pause. The online voice rests for over a second at one (measured 8 Nov 2026: 1.12 s,
  where a space made no pause at all), so every new line becomes an ordinary space.
* Times are said as times: "08.00-16.30 น." is "แปดโมงเช้า ถึง สี่โมงครึ่ง", not read digit by digit.
* A dash between two Thai words or two numbers means "ถึง" ("จันทร์-ศุกร์", "10-11"), a slash is a space,
  brackets and quotes are dropped (their words are kept), "%" and "&" are said.

Only the TEXT SENT TO THE VOICE changes; the screen shows what was written.
"""
from __future__ import annotations

import re

_ONES = ("ศูนย์", "หนึ่ง", "สอง", "สาม", "สี่", "ห้า", "หก", "เจ็ด", "แปด", "เก้า")
_THAI = "ก-๙"


def thai_number(n: int) -> str:
    """0 - 99 in Thai words: ยี่สิบ, สิบเอ็ด, ยี่สิบเอ็ด, ..."""
    if n < 10:
        return _ONES[n]
    tens, ones = divmod(n, 10)
    word = "สิบ" if tens == 1 else "ยี่สิบ" if tens == 2 else _ONES[tens] + "สิบ"
    if ones == 0:
        return word
    return word + ("เอ็ด" if ones == 1 else _ONES[ones])


def thai_clock(hour: int, minute: int) -> str:
    """"แปดโมงเช้า", "บ่ายโมง", "สี่โมงครึ่ง", "ห้าทุ่มสิบห้านาที", "เที่ยง", "ตีสอง"."""
    hour %= 24
    if hour == 0:
        base = "เที่ยงคืน"
    elif hour <= 5:
        base = "ตี" + thai_number(hour)
    elif hour <= 11:
        base = thai_number(hour) + "โมงเช้า"
    elif hour == 12:
        base = "เที่ยง"
    elif hour == 13:
        base = "บ่ายโมง"
    elif hour <= 15:
        base = "บ่าย" + thai_number(hour - 12) + "โมง"
    elif hour <= 18:
        base = thai_number(hour - 12) + "โมงเย็น"
    else:
        base = thai_number(hour - 18) + "ทุ่ม"
    if minute == 0:
        return base
    if minute == 30:
        return base.replace("โมงเช้า", "โมง").replace("โมงเย็น", "โมง") + "ครึ่ง"
    return base + thai_number(minute) + "นาที"


_HOUR_MINUTE = r"([01]?\d|2[0-4])[.:]([0-5]\d)(?!\d)"
_CLOCK = r"(?<![\d.:])" + _HOUR_MINUTE
_RANGE_OF_TIMES = re.compile(_CLOCK + r"\s*(?:น\.?)?\s*[-–—~]\s*" + _HOUR_MINUTE + r"\s*(?:น\.?)?")
_TIME_WITH_NO = re.compile(_CLOCK + r"\s*น(?:\.|(?![" + _THAI + r"]))")
_TIME_AFTER_WORD = re.compile(r"(เวลา|ตั้งแต่|ถึง|เปิด|ปิด|ประมาณ|หลัง|ก่อน)\s*" + _HOUR_MINUTE)
_DASH_BETWEEN_THAI = re.compile(r"(?<=[" + _THAI + r"])\s*[-–—]\s*(?=[" + _THAI + r"])")
_DASH_BETWEEN_NUMBERS = re.compile(r"(?<=\d)\s*[-–—]\s*(?=\d)")
_BRACKETS = re.compile(r"[()\[\]{}“”\"«»]")
_SPACES = re.compile(r"[ \t]+")


def to_spoken_thai(text: str) -> str:
    """``text`` as it should be read aloud in Thai (see the module's note)."""
    text = re.sub(r"[ \t]*\n+[ \t]*", " ", text)
    text = _RANGE_OF_TIMES.sub(lambda m: f"{thai_clock(int(m[1]), int(m[2]))} ถึง {thai_clock(int(m[3]), int(m[4]))}", text)
    text = _TIME_WITH_NO.sub(lambda m: thai_clock(int(m[1]), int(m[2])), text)
    text = _TIME_AFTER_WORD.sub(lambda m: f"{m[1]} {thai_clock(int(m[2]), int(m[3]))}", text)
    text = _DASH_BETWEEN_THAI.sub(" ถึง ", text)
    text = _DASH_BETWEEN_NUMBERS.sub(" ถึง ", text)
    text = re.sub(r"\.{2,}|…", " ", text)  # a trailing "..." made the online Thai voice answer with no audio at all
    text = re.sub(r"\.{2,}|…", " ", text)  # a "..." made the online Thai voice answer with no audio at all (8 Nov)
    text = text.replace("ฯลฯ", "และอื่นๆ").replace("%", " เปอร์เซ็นต์").replace("&", " และ ")
    text = re.sub(r"\s*/\s*", " ", text)
    text = _BRACKETS.sub(" ", text)
    return _SPACES.sub(" ", text).strip()


def to_spoken_english(text: str) -> str:
    """The same for English: a new line is an ordinary space, brackets and quotes go."""
    text = re.sub(r"[ \t]*\n+[ \t]*", " ", text)
    text = re.sub(r"[“”\"«»]", "", text)
    return _SPACES.sub(" ", text.replace("(", ", ").replace(")", ",")).strip()
