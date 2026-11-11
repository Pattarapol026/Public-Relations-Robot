"""Knowledge base to and from a CSV file (opens in Excel), and the date format used for expiry.

Moving a robot to a new site means typing in a new set of questions and
answers; doing it in a spreadsheet is much faster than one dialog at a time.
"""
from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Iterable

from database.models import KnowledgeItem
from services import venues

BUDDHIST_ERA_OFFSET = 543
MAX_IMPORT_ROWS = 2000

# Column titles in the file; English names are accepted on import too. The kind
# of place comes last so files from before it existed still line up.
COLUMNS = (
    "หมวด", "คำถาม", "คำตอบ", "คำค้น", "ปุ่มลัด", "เปิดใช้งาน", "หมดอายุ", "ประเภทสถานที่",
    "คำถาม (อังกฤษ)", "คำตอบ (อังกฤษ)",
)
_ALIASES = {
    "คำถาม (อังกฤษ)": "question_en", "question (english)": "question_en", "question_en": "question_en",
    "คำตอบ (อังกฤษ)": "answer_en", "answer (english)": "answer_en", "answer_en": "answer_en",
    "ประเภทสถานที่": "venue", "หมวดประชาสัมพันธ์": "venue", "venue": "venue",
    "หมวด": "category", "category": "category",
    "คำถาม": "question", "คำถาม / หัวข้อ": "question", "question": "question",
    "คำตอบ": "answer", "answer": "answer",
    "คำค้น": "keywords", "keywords": "keywords",
    "ปุ่มลัด": "shortcut", "shortcut": "shortcut",
    "เปิดใช้งาน": "enabled", "enabled": "enabled",
    "หมดอายุ": "expires_on", "expires": "expires_on", "expires_on": "expires_on",
}
_YES = {"ใช่", "yes", "y", "true", "1", "x", "✓"}
_NO = {"ไม่", "no", "n", "false", "0", "ปิด"}
_FORMULA_STARTS = ("=", "+", "-", "@", "\t", "\r")
_ISO = re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})$")
_DMY = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4})$")


@dataclass(frozen=True)
class KnowledgeRow:
    category: str
    question: str
    answer: str
    keywords: str  # comma-separated, as typed in the Admin form
    shortcut: bool
    enabled: bool
    expires_on: str  # YYYY-MM-DD or ""
    venue: str | None = None  # services.venues id, "" = every kind; None = the file does not say
    question_en: str = ""
    answer_en: str = ""


def parse_date(text: str) -> str:
    """'2026-12-31', '31/12/2026' or a Buddhist-era year ('31/12/2569') -> '2026-12-31'.

    Empty text means "no date". Raises ``ValueError`` with a message for the Admin.
    """
    text = text.strip()
    if not text:
        return ""
    match = _ISO.match(text)
    if match:
        year, month, day = (int(part) for part in match.groups())
    else:
        match = _DMY.match(text)
        if not match:
            raise ValueError(f"วันที่ \"{text}\" ไม่ถูกต้อง ใช้รูปแบบ 31/12/2569 หรือ 2026-12-31")
        day, month, year = (int(part) for part in match.groups())
    if year > 2400:
        year -= BUDDHIST_ERA_OFFSET
    try:
        return date(year, month, day).isoformat()
    except ValueError:
        raise ValueError(f"ไม่มีวันที่ \"{text}\" ในปฏิทิน") from None


def thai_date(iso: str) -> str:
    """'2026-12-31' -> '31/12/2569' for display."""
    if not iso:
        return ""
    year, month, day = (int(part) for part in iso.split("-"))
    return f"{day:02d}/{month:02d}/{year + BUDDHIST_ERA_OFFSET}"


def export_csv(path: Path, items: Iterable[KnowledgeItem]) -> int:
    """Write the knowledge base; returns how many entries were written."""
    count = 0
    with Path(path).open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(COLUMNS)
        for item in items:
            writer.writerow([
                _plain(item.category), _plain(item.question), _plain(item.answer),
                _plain(", ".join(item.keywords)),
                "ใช่" if item.show_as_shortcut else "", "ใช่" if item.enabled else "ไม่",
                thai_date(item.expires_on), venues.label(item.venue),
                _plain(item.question_en), _plain(item.answer_en),
            ])
            count += 1
    return count


def read_csv(path: Path) -> tuple[list[KnowledgeRow], list[str]]:
    """Rows that can be imported, and a message for each row that cannot.

    Raises ``ValueError`` when the file as a whole cannot be used.
    """
    try:
        with Path(path).open(encoding="utf-8-sig", newline="") as handle:
            lines = list(csv.reader(handle))
    except UnicodeDecodeError:
        raise ValueError("อ่านไฟล์ไม่ได้ กรุณาบันทึกจาก Excel เป็น \"CSV UTF-8\"") from None
    if not lines:
        raise ValueError("ไฟล์ว่างเปล่า")
    columns = [_ALIASES.get(title.strip().lower(), _ALIASES.get(title.strip())) for title in lines[0]]
    if "question" not in columns or "answer" not in columns:
        raise ValueError("ไม่พบคอลัมน์ \"คำถาม\" และ \"คำตอบ\" ในแถวแรกของไฟล์")
    if len(lines) - 1 > MAX_IMPORT_ROWS:
        raise ValueError(f"นำเข้าได้ครั้งละไม่เกิน {MAX_IMPORT_ROWS} แถว")

    rows: list[KnowledgeRow] = []
    errors: list[str] = []
    for number, line in enumerate(lines[1:], start=2):
        cells = {name: _unplain(value) for name, value in zip(columns, line) if name}
        if not any(cells.values()):
            continue  # blank line
        question, answer = cells.get("question", ""), cells.get("answer", "")
        if not question or not answer:
            errors.append(f"แถว {number}: ต้องมีทั้งคำถามและคำตอบ")
            continue
        try:
            expires_on = parse_date(cells.get("expires_on", ""))
        except ValueError as exc:
            errors.append(f"แถว {number}: {exc}")
            continue
        venue = None  # not given: the importer uses the kind of place in use
        if cells.get("venue", "").strip():
            venue = venues.from_text(cells["venue"])
            if venue is None:
                errors.append(f"แถว {number}: ไม่รู้จักประเภทสถานที่ \"{cells['venue']}\"")
                continue
        rows.append(
            KnowledgeRow(
                category=cells.get("category", ""), question=question, answer=answer,
                keywords=cells.get("keywords", ""),
                shortcut=cells.get("shortcut", "").lower() in _YES,
                enabled=cells.get("enabled", "").lower() not in _NO,
                expires_on=expires_on, venue=venue,
                question_en=cells.get("question_en", ""), answer_en=cells.get("answer_en", ""),
            )
        )
    return rows, errors


def question_key(question: str) -> str:
    """Questions that differ only in spacing or letter case are the same entry."""
    return "".join(question.split()).casefold()


def _plain(text: str) -> str:
    """Stop a spreadsheet from reading a cell as a formula."""
    return "'" + text if text.startswith(_FORMULA_STARTS) else text


def _unplain(text: str) -> str:
    text = text.strip()
    return text[1:] if text.startswith("'") and text[1:2] in _FORMULA_STARTS else text
