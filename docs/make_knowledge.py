"""Builds docs/knowledge.csv from docs/knowledge_data/*.py, ready for Admin → ฐานความรู้ → นำเข้า.

Run:  .venv313\\Scripts\\python.exe docs\\make_knowledge.py

The file has the same columns as Admin's export, with each entry's kind of place
and its English version. Importing it updates an entry that has the same question
for the same kind of place, and adds the rest.
"""
from __future__ import annotations

import csv
import importlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from services.knowledge_transfer import COLUMNS, question_key  # noqa: E402
from services.venues import SHARED  # noqa: E402

MODULES = ("shared", "general", "education", "hospital", "hotel", "mall", "government", "office")
YES = "ใช่"
_FORBIDDEN = ("ตัวอย่าง", "DEMO", "SAMPLE")


def entries() -> list[tuple[str, tuple]]:
    """(venue, entry) for every entry, checked."""
    found, seen, problems = [], set(), []
    for name in MODULES:
        module = importlib.import_module(f"docs.knowledge_data.{name}")
        for entry in module.ENTRIES:
            category, question, answer, question_en, answer_en, _keywords, _shortcut = entry
            key = (module.VENUE, question_key(question))
            if key in seen:
                problems.append(f"{name}: duplicate question {question}")
            seen.add(key)
            if not (question and answer and question_en and answer_en):
                problems.append(f"{name}: missing Thai or English text: {question}")
            if any(mark in f"{category} {answer} {answer_en}".upper() for mark in _FORBIDDEN):
                problems.append(f"{name}: example label left in: {question}")
            found.append((module.VENUE, entry))
    if problems:
        raise SystemExit("\n".join(problems))
    return found


def main() -> None:
    rows = entries()
    path = ROOT / "docs" / "knowledge.csv"
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(COLUMNS)
        for venue, (category, question, answer, question_en, answer_en, keywords, shortcut) in rows:
            writer.writerow([
                category, question, answer, keywords, YES if shortcut else "", YES, "",
                "ใช้ร่วมทุกหมวด" if venue == SHARED else venue, question_en, answer_en,
            ])
    counts: dict[str, int] = {}
    for venue, _entry in rows:
        counts[venue or "shared"] = counts.get(venue or "shared", 0) + 1
    print(f"wrote {len(rows)} entries to {path}: {counts}")


if __name__ == "__main__":
    main()
