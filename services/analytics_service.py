"""Usage statistics: what visitors asked, how well the robot answered, and who came by.

Nothing here identifies a person. A question's text is kept only so that the
Admin can see what the robot failed to answer and add it to the knowledge base;
that can be switched off, the text can be erased, and old rows are purged.
"""
from __future__ import annotations

import csv
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable

from database.models import Interaction
from database.repositories import InteractionRepository, VisitRepository
from services.conversation_service import SOURCE_FALLBACK, SOURCE_NO_INFO, ConversationResult

logger = logging.getLogger(__name__)

TIME_FORMAT = "%Y-%m-%dT%H:%M:%S"

# Which conversations a search returns.
KIND_ALL = "all"
KIND_UNANSWERED = "unanswered"  # the robot had no answer
# Sources of an interaction in which the visitor got no answer to the question.
UNANSWERED_SOURCES = (SOURCE_FALLBACK, SOURCE_NO_INFO)
KIND_DISLIKED = "disliked"  # the visitor (or the Admin) said the answer was not right
KIND_AI_ERROR = "ai_error"  # the AI could not be used for it
MAX_STORED_CHARS = 500
_FORMULA_STARTS = ("=", "+", "-", "@", "\t", "\r")


def _as_plain_cell(text: str) -> str:
    """Visitors' words go into a spreadsheet: stop it reading them as a formula."""
    return "'" + text if text.startswith(_FORMULA_STARTS) else text


@dataclass(frozen=True)
class UsageSummary:
    days: int
    visits: int = 0
    engaged_visits: int = 0
    average_visit_s: float = 0.0
    questions: int = 0
    answered: int = 0  # the robot had an answer (anything but the "I don't know" fallback)
    feedback_up: int = 0
    feedback_down: int = 0
    ai_failures: int = 0  # Gemini was tried and failed; the knowledge base answered instead
    average_response_ms: int = 0
    by_source: dict[str, int] = field(default_factory=dict)
    by_mode: dict[str, int] = field(default_factory=dict)
    by_language: dict[str, int] = field(default_factory=dict)
    per_hour: tuple[int, ...] = (0,) * 24  # questions by hour of day, 0–23

    @property
    def answer_rate(self) -> float | None:
        return self.answered / self.questions if self.questions else None

    @property
    def satisfaction(self) -> float | None:
        rated = self.feedback_up + self.feedback_down
        return self.feedback_up / rated if rated else None

    @property
    def engagement_rate(self) -> float | None:
        return self.engaged_visits / self.visits if self.visits else None


class AnalyticsService:
    def __init__(
        self,
        interactions: InteractionRepository,
        visits: VisitRepository,
        log_questions: bool = True,
        retention_days: int = 90,
        clock: Callable[[], datetime] = datetime.now,
    ) -> None:
        self._interactions = interactions
        self._visits = visits
        self._log_questions = log_questions
        self._retention_days = retention_days
        self._clock = clock

    @property
    def logs_questions(self) -> bool:
        return self._log_questions

    @property
    def retention_days(self) -> int:
        return self._retention_days

    # --- recording ---

    def record_interaction(
        self, question: str, result: ConversationResult, response_ms: int, input_mode: str
    ) -> int:
        keep_text = self._log_questions
        return self._interactions.add(
            created_at=self._clock().strftime(TIME_FORMAT),
            question=question[:MAX_STORED_CHARS] if keep_text else "",
            answer=result.answer_text[:MAX_STORED_CHARS] if keep_text else "",
            source=result.source,
            language=result.language,
            input_mode=input_mode,
            ai_error=result.ai_error or "",
            response_ms=max(0, response_ms),
        )

    def record_feedback(self, interaction_id: int, positive: bool) -> None:
        self._interactions.set_feedback(interaction_id, 1 if positive else -1)

    def record_visit(
        self, duration_s: float, closest_m: float | None, engaged: bool, questions: int
    ) -> None:
        started = self._clock() - timedelta(seconds=max(0.0, duration_s))
        self._visits.add(started.strftime(TIME_FORMAT), duration_s, closest_m, engaged, questions)

    # --- reading ---

    def summary(self, days: int) -> UsageSummary:
        """Statistics for the last ``days`` calendar days, today included."""
        since = self._since(days)
        interactions = self._interactions.list_since(since)
        visits, engaged, average_visit = self._visits.summary_since(since)

        by_source: dict[str, int] = {}
        by_mode: dict[str, int] = {}
        by_language: dict[str, int] = {}
        per_hour = [0] * 24
        for item in interactions:
            by_source[item.source] = by_source.get(item.source, 0) + 1
            by_mode[item.input_mode] = by_mode.get(item.input_mode, 0) + 1
            by_language[item.language] = by_language.get(item.language, 0) + 1
            try:
                per_hour[datetime.strptime(item.created_at, TIME_FORMAT).hour] += 1
            except ValueError:
                pass
        count = len(interactions)
        return UsageSummary(
            days=days,
            visits=visits,
            engaged_visits=engaged,
            average_visit_s=average_visit,
            questions=count,
            answered=sum(1 for item in interactions if item.source not in UNANSWERED_SOURCES),
            feedback_up=sum(1 for item in interactions if item.feedback > 0),
            feedback_down=sum(1 for item in interactions if item.feedback < 0),
            ai_failures=sum(1 for item in interactions if item.ai_error),
            average_response_ms=round(sum(i.response_ms for i in interactions) / count) if count else 0,
            by_source=by_source,
            by_mode=by_mode,
            by_language=by_language,
            per_hour=tuple(per_hour),
        )

    def top_questions(self, days: int, limit: int = 5) -> list[tuple[str, int]]:
        """The most asked questions of the period, as (question, times asked), most asked first.

        Questions that differ only in spacing or letter case count as one (Thai
        is written without fixed word spaces, and speech-to-text places them
        differently each time); the wording shown is the one used most
        recently. Empty when question text is not being kept.
        """
        counts: dict[str, int] = {}
        wording: dict[str, str] = {}
        for item in self._interactions.list_since(self._since(days)):
            text = " ".join(item.question.split())
            if not text:
                continue
            key = "".join(text.split()).casefold()
            counts[key] = counts.get(key, 0) + 1
            wording[key] = text
        ranked = sorted(counts.items(), key=lambda pair: -pair[1])[:limit]
        return [(wording[key], count) for key, count in ranked]

    def needing_attention(self, days: int, limit: int = 100) -> list[Interaction]:
        """Questions to feed back into the knowledge base: unanswered, or answered badly."""
        return self._interactions.needing_attention(self._since(days), limit)

    def resolve(self, interaction_id: int) -> None:
        self._interactions.set_resolved(interaction_id)

    def search(self, days: int, text: str = "", kind: str = KIND_ALL, limit: int = 500) -> list[Interaction]:
        """Conversations of the period, newest first, optionally only those containing ``text``
        (in the question or the answer) and of one kind (see the KIND_ constants)."""
        needle = text.strip().casefold()
        matches = []
        for item in reversed(self._interactions.list_since(self._since(days))):
            if needle and needle not in item.question.casefold() and needle not in item.answer.casefold():
                continue
            if kind == KIND_UNANSWERED and item.source not in UNANSWERED_SOURCES:
                continue
            if kind == KIND_DISLIKED and item.feedback >= 0:
                continue
            if kind == KIND_AI_ERROR and not item.ai_error:
                continue
            matches.append(item)
            if len(matches) >= limit:
                break
        return matches

    def mark_wrong(self, interaction_id: int) -> None:
        """The Admin judged this answer wrong: list it with the questions to fix."""
        self._interactions.set_feedback(interaction_id, -1)
        self._interactions.set_resolved(interaction_id, False)

    def export_csv(self, path: Path, days: int) -> int:
        """Write the period's interactions to a CSV file (opens correctly in Excel). Returns rows."""
        return self.export_rows(path, self._interactions.list_since(self._since(days)))

    def export_rows(self, path: Path, interactions: list[Interaction]) -> int:
        """Write these interactions to a CSV file (opens correctly in Excel). Returns rows."""
        with Path(path).open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(
                ["เวลา", "คำถาม", "คำตอบ", "ที่มาของคำตอบ", "ภาษา", "ช่องทาง",
                 "เวลาตอบ (มิลลิวินาที)", "คำติชม", "ข้อผิดพลาดของ AI"]
            )
            for item in interactions:
                writer.writerow(
                    [item.created_at.replace("T", " "), _as_plain_cell(item.question),
                     _as_plain_cell(item.answer), item.source,
                     item.language, item.input_mode, item.response_ms,
                     {1: "ช่วยได้", -1: "ยังไม่ตรง"}.get(item.feedback, ""), item.ai_error]
                )
        return len(interactions)

    # --- housekeeping ---

    def purge_old(self) -> int:
        """Delete rows older than the retention period. Returns how many were removed."""
        before = (self._clock() - timedelta(days=self._retention_days)).strftime(TIME_FORMAT)
        removed = self._interactions.delete_before(before) + self._visits.delete_before(before)
        if removed:
            logger.info("Purged %d usage rows older than %d days", removed, self._retention_days)
        return removed

    def clear_question_text(self) -> int:
        return self._interactions.clear_text()

    def _since(self, days: int) -> str:
        start = (self._clock() - timedelta(days=max(1, days) - 1)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        return start.strftime(TIME_FORMAT)
