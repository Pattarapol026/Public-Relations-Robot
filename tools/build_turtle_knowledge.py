"""Writes docs/turtle_knowledge.csv: questions and answers for the turtle conservation centre.

Source: the sheet of 110 questions and answers sent on 7 Nov 2026 about the Sea Turtle Conservation
Centre of the Royal Thai Navy (Sattahip, Chonburi): parts a-c, kept as sent (the trailing "ครับ" is dropped
from the questions). Part d is extra questions visitors are likely to ask; its facts about the centre come
only from the sheet, and where the sheet is silent the answer says so and points to 038-431-477.
Answers are written to be spoken: short, Arabic digits.

    python -m tools.build_turtle_knowledge
Then Admin -> ฐานความรู้ -> นำเข้า CSV (entries with the same question are updated, not duplicated).
"""
from __future__ import annotations

import csv
from pathlib import Path

from services.knowledge_transfer import COLUMNS
from tools.turtle_knowledge import part_a, part_b, part_c, part_d, part_e

VENUE = "ศูนย์อนุรักษ์พันธุ์เต่า"
OUT = Path(__file__).resolve().parent.parent / "docs" / "turtle_knowledge.csv"

ENTRIES = [*part_a.ENTRIES, *part_b.ENTRIES, *part_c.ENTRIES, *part_d.ENTRIES, *part_e.ENTRIES]


def check() -> None:
    seen: set[str] = set()
    for entry in ENTRIES:
        assert len(entry) == 7, entry[:2]
        category, question, answer, keywords, _shortcut, question_en, answer_en = entry
        assert category and question and answer and question_en and answer_en, question
        assert question not in seen, f"duplicate question: {question}"
        seen.add(question)


def main() -> None:
    check()
    with OUT.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.writer(file)
        writer.writerow(COLUMNS)
        for category, question, answer, keywords, shortcut, question_en, answer_en in ENTRIES:
            writer.writerow([category, question, answer, keywords, "ใช่" if shortcut else "ไม่", "ใช่", "", VENUE,
                             question_en, answer_en])
    print(f"{len(ENTRIES)} entries -> {OUT}")


if __name__ == "__main__":
    main()
