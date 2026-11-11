"""All SQL lives here; the rest of the app works with the model dataclasses."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Iterable

from database.db import Database
from database.models import (
    ActivityLog,
    Announcement,
    Interaction,
    KnowledgeItem,
    MediaItem,
    Schedule,
    VisualTrigger,
)

MOVE_UP = -1
MOVE_DOWN = 1


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _next_order(connection: sqlite3.Connection, table: str) -> int:
    row = connection.execute(f"SELECT COALESCE(MAX(sort_order), 0) + 1 FROM {table}").fetchone()
    return row[0]


def _move(connection: sqlite3.Connection, table: str, item_id: int, direction: int) -> bool:
    """Swap ``sort_order`` with the neighbour above/below. ``table`` is a code constant."""
    current = connection.execute(
        f"SELECT id, sort_order FROM {table} WHERE id = ?", (item_id,)
    ).fetchone()
    if current is None:
        return False
    if direction < 0:
        neighbour_sql = (
            f"SELECT id, sort_order FROM {table} WHERE (sort_order, id) < (?, ?) "
            "ORDER BY sort_order DESC, id DESC LIMIT 1"
        )
    else:
        neighbour_sql = (
            f"SELECT id, sort_order FROM {table} WHERE (sort_order, id) > (?, ?) "
            "ORDER BY sort_order ASC, id ASC LIMIT 1"
        )
    neighbour = connection.execute(neighbour_sql, (current["sort_order"], current["id"])).fetchone()
    if neighbour is None:
        return False
    connection.execute(
        f"UPDATE {table} SET sort_order = ? WHERE id = ?", (neighbour["sort_order"], current["id"])
    )
    connection.execute(
        f"UPDATE {table} SET sort_order = ? WHERE id = ?", (current["sort_order"], neighbour["id"])
    )
    return True


class MediaRepository:
    _TABLE = "media_items"

    def __init__(self, database: Database) -> None:
        self._db = database

    def add(
        self,
        title: str,
        description: str,
        category: str,
        media_type: str,
        file_name: str,
        original_name: str,
        size_bytes: int,
    ) -> MediaItem:
        now = _now()
        with self._db.connect() as connection:
            cursor = connection.execute(
                "INSERT INTO media_items (title, description, category, media_type, file_name, "
                "original_name, size_bytes, enabled, sort_order, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?)",
                (title, description, category, media_type, file_name, original_name, size_bytes,
                 _next_order(connection, self._TABLE), now, now),
            )
            row = connection.execute(
                "SELECT * FROM media_items WHERE id = ?", (cursor.lastrowid,)
            ).fetchone()
        return self._to_model(row)

    def get(self, item_id: int) -> MediaItem | None:
        with self._db.connect() as connection:
            row = connection.execute(
                "SELECT * FROM media_items WHERE id = ?", (item_id,)
            ).fetchone()
        return self._to_model(row) if row else None

    def list(self, enabled_only: bool = False) -> list[MediaItem]:
        where = "WHERE enabled = 1" if enabled_only else ""
        with self._db.connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM media_items {where} ORDER BY sort_order, id"
            ).fetchall()
        return [self._to_model(row) for row in rows]

    def update(
        self, item_id: int, title: str, description: str, category: str, enabled: bool
    ) -> None:
        with self._db.connect() as connection:
            connection.execute(
                "UPDATE media_items SET title = ?, description = ?, category = ?, enabled = ?, "
                "updated_at = ? WHERE id = ?",
                (title, description, category, int(enabled), _now(), item_id),
            )

    def delete(self, item_id: int) -> None:
        with self._db.connect() as connection:
            connection.execute("DELETE FROM media_items WHERE id = ?", (item_id,))

    def move(self, item_id: int, direction: int) -> bool:
        with self._db.connect() as connection:
            return _move(connection, self._TABLE, item_id, direction)

    @staticmethod
    def _to_model(row: sqlite3.Row) -> MediaItem:
        return MediaItem(
            id=row["id"], title=row["title"], description=row["description"],
            category=row["category"], media_type=row["media_type"], file_name=row["file_name"],
            original_name=row["original_name"], size_bytes=row["size_bytes"],
            enabled=bool(row["enabled"]), sort_order=row["sort_order"],
        )


class AnnouncementRepository:
    _TABLE = "announcements"

    def __init__(self, database: Database) -> None:
        self._db = database

    def add(self, title: str, body: str, enabled: bool = True, venue: str = "") -> int:
        now = _now()
        with self._db.connect() as connection:
            cursor = connection.execute(
                "INSERT INTO announcements (title, body, enabled, venue, sort_order, created_at, "
                "updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (title, body, int(enabled), venue, _next_order(connection, self._TABLE), now, now),
            )
            return cursor.lastrowid

    def list(self, enabled_only: bool = False, venue: str | None = None) -> list[Announcement]:
        """With ``venue``, only that kind of place's announcements and the shared ones ('')."""
        conditions, parameters = [], []
        if enabled_only:
            conditions.append("enabled = 1")
        if venue is not None:
            conditions.append("venue IN (?, '')")
            parameters.append(venue)
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        with self._db.connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM announcements {where} ORDER BY sort_order, id", parameters
            ).fetchall()
        return [
            Announcement(row["id"], row["title"], row["body"], bool(row["enabled"]),
                         row["sort_order"], row["venue"])
            for row in rows
        ]

    def update(
        self, item_id: int, title: str, body: str, enabled: bool, venue: str | None = None
    ) -> None:
        """``venue`` None keeps the announcement's kind of place as it is."""
        venue_sql, venue_args = (", venue = ?", [venue]) if venue is not None else ("", [])
        with self._db.connect() as connection:
            connection.execute(
                f"UPDATE announcements SET title = ?, body = ?, enabled = ?{venue_sql}, "
                "updated_at = ? WHERE id = ?",
                (title, body, int(enabled), *venue_args, _now(), item_id),
            )

    def delete(self, item_id: int) -> None:
        with self._db.connect() as connection:
            connection.execute("DELETE FROM announcements WHERE id = ?", (item_id,))

    def move(self, item_id: int, direction: int) -> bool:
        with self._db.connect() as connection:
            return _move(connection, self._TABLE, item_id, direction)


class KnowledgeRepository:
    def __init__(self, database: Database) -> None:
        self._db = database

    def add(
        self,
        category: str,
        question: str,
        answer: str,
        keywords: Iterable[str] = (),
        enabled: bool = True,
        show_as_shortcut: bool = False,
        expires_on: str = "",
        venue: str = "",
        question_en: str = "",
        answer_en: str = "",
    ) -> int:
        now = _now()
        with self._db.connect() as connection:
            cursor = connection.execute(
                "INSERT INTO knowledge_items (category, question, answer, keywords, enabled, "
                "show_as_shortcut, expires_on, venue, question_en, answer_en, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (category, question, answer, _join_keywords(keywords), int(enabled),
                 int(show_as_shortcut), expires_on, venue, question_en, answer_en, now, now),
            )
            return cursor.lastrowid

    def list(self, enabled_only: bool = False, venue: str | None = None) -> list[KnowledgeItem]:
        """All entries; with ``enabled_only``, just those in use today (enabled, not expired).

        With ``venue``, only that kind of place's entries and the shared ones ('').
        """
        conditions, parameters = [], []
        if enabled_only:
            conditions.append("enabled = 1 AND (expires_on = '' OR expires_on >= date('now', 'localtime'))")
        if venue is not None:
            conditions.append("venue IN (?, '')")
            parameters.append(venue)
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        with self._db.connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM knowledge_items {where} ORDER BY category, id", parameters
            ).fetchall()
        return [
            KnowledgeItem(
                id=row["id"], category=row["category"], question=row["question"],
                answer=row["answer"], keywords=split_keywords(row["keywords"]),
                enabled=bool(row["enabled"]), show_as_shortcut=bool(row["show_as_shortcut"]),
                expires_on=row["expires_on"], venue=row["venue"],
                question_en=row["question_en"], answer_en=row["answer_en"],
            )
            for row in rows
        ]

    def update(
        self,
        item_id: int,
        category: str,
        question: str,
        answer: str,
        keywords: Iterable[str],
        enabled: bool,
        show_as_shortcut: bool,
        expires_on: str = "",
        venue: str | None = None,
        question_en: str | None = None,
        answer_en: str | None = None,
    ) -> None:
        """``venue``, ``question_en``, ``answer_en``: None keeps the stored value as it is."""
        extra_sql, extra_args = "", []
        for column, value in (("venue", venue), ("question_en", question_en), ("answer_en", answer_en)):
            if value is not None:
                extra_sql += f", {column} = ?"
                extra_args.append(value)
        with self._db.connect() as connection:
            connection.execute(
                "UPDATE knowledge_items SET category = ?, question = ?, answer = ?, keywords = ?, "
                f"enabled = ?, show_as_shortcut = ?, expires_on = ?{extra_sql}, updated_at = ? "
                "WHERE id = ?",
                (category, question, answer, _join_keywords(keywords), int(enabled),
                 int(show_as_shortcut), expires_on, *extra_args, _now(), item_id),
            )

    def delete(self, item_id: int) -> None:
        with self._db.connect() as connection:
            connection.execute("DELETE FROM knowledge_items WHERE id = ?", (item_id,))


def split_keywords(text: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in text.split(",") if part.strip())


def _join_keywords(keywords: Iterable[str]) -> str:
    return ",".join(keyword.strip() for keyword in keywords if keyword.strip())


class SettingsRepository:
    def __init__(self, database: Database) -> None:
        self._db = database

    def get(self, key: str, default: str | None = None) -> str | None:
        with self._db.connect() as connection:
            row = connection.execute(
                "SELECT value FROM app_settings WHERE key = ?", (key,)
            ).fetchone()
        return row["value"] if row else default

    def all(self) -> dict[str, str]:
        with self._db.connect() as connection:
            rows = connection.execute("SELECT key, value FROM app_settings ORDER BY key").fetchall()
        return {row["key"]: row["value"] for row in rows}

    def set(self, key: str, value: str) -> None:
        with self._db.connect() as connection:
            connection.execute(
                "INSERT INTO app_settings (key, value, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT (key) DO UPDATE SET value = excluded.value, "
                "updated_at = excluded.updated_at",
                (key, value, _now()),
            )


class AdminUserRepository:
    """Credential store for ``AdminAuth``. V0.2 has one account: ``admin``."""

    USERNAME = "admin"

    def __init__(self, database: Database) -> None:
        self._db = database

    def load(self) -> dict | None:
        with self._db.connect() as connection:
            row = connection.execute(
                "SELECT password_hash FROM admin_users WHERE username = ?", (self.USERNAME,)
            ).fetchone()
        if row is None:
            return None
        try:
            record = json.loads(row["password_hash"])
        except json.JSONDecodeError:
            return None
        return record if isinstance(record, dict) else None

    def save(self, record: dict) -> None:
        now = _now()
        with self._db.connect() as connection:
            connection.execute(
                "INSERT INTO admin_users (username, password_hash, created_at, updated_at) "
                "VALUES (?, ?, ?, ?) ON CONFLICT (username) DO UPDATE SET "
                "password_hash = excluded.password_hash, updated_at = excluded.updated_at",
                (self.USERNAME, json.dumps(record), now, now),
            )


class VisualTriggerRepository:
    def __init__(self, database: Database) -> None:
        self._db = database

    def list(self) -> list[VisualTrigger]:
        with self._db.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM visual_triggers ORDER BY kind, CAST(code AS INTEGER), code"
            ).fetchall()
        return [self._to_model(row) for row in rows]

    def find(self, kind: str, code: str) -> VisualTrigger | None:
        with self._db.connect() as connection:
            row = connection.execute(
                "SELECT * FROM visual_triggers WHERE kind = ? AND code = ?", (kind, code)
            ).fetchone()
        return self._to_model(row) if row else None

    def save(
        self,
        trigger_id: int | None,
        kind: str,
        code: str,
        title: str,
        action: str,
        response_text: str,
        media_id: int | None,
        enabled: bool,
    ) -> int:
        """Insert or update. Raises ``sqlite3.IntegrityError`` if (kind, code) is already taken."""
        now = _now()
        with self._db.connect() as connection:
            if trigger_id is None:
                cursor = connection.execute(
                    "INSERT INTO visual_triggers (kind, code, title, action, response_text, "
                    "media_id, enabled, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (kind, code, title, action, response_text, media_id, int(enabled), now, now),
                )
                return cursor.lastrowid
            connection.execute(
                "UPDATE visual_triggers SET kind = ?, code = ?, title = ?, action = ?, "
                "response_text = ?, media_id = ?, enabled = ?, updated_at = ? WHERE id = ?",
                (kind, code, title, action, response_text, media_id, int(enabled), now, trigger_id),
            )
            return trigger_id

    def delete(self, trigger_id: int) -> None:
        with self._db.connect() as connection:
            connection.execute("DELETE FROM visual_triggers WHERE id = ?", (trigger_id,))

    @staticmethod
    def _to_model(row: sqlite3.Row) -> VisualTrigger:
        return VisualTrigger(
            id=row["id"], kind=row["kind"], code=row["code"], title=row["title"],
            action=row["action"], response_text=row["response_text"], media_id=row["media_id"],
            enabled=bool(row["enabled"]),
        )


class InteractionRepository:
    def __init__(self, database: Database) -> None:
        self._db = database

    def add(
        self,
        created_at: str,
        question: str,
        answer: str,
        source: str,
        language: str,
        input_mode: str,
        ai_error: str,
        response_ms: int,
    ) -> int:
        with self._db.connect() as connection:
            cursor = connection.execute(
                "INSERT INTO interactions (created_at, question, answer, source, language, "
                "input_mode, ai_error, response_ms) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (created_at, question, answer, source, language, input_mode, ai_error, response_ms),
            )
            return cursor.lastrowid

    def set_feedback(self, interaction_id: int, feedback: int) -> None:
        with self._db.connect() as connection:
            connection.execute(
                "UPDATE interactions SET feedback = ? WHERE id = ?", (feedback, interaction_id)
            )

    def set_resolved(self, interaction_id: int, resolved: bool = True) -> None:
        with self._db.connect() as connection:
            connection.execute(
                "UPDATE interactions SET resolved = ? WHERE id = ?", (int(resolved), interaction_id)
            )

    def list_since(self, since: str) -> list[Interaction]:
        """Interactions from ``since`` (local ISO time) onwards, oldest first."""
        with self._db.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM interactions WHERE created_at >= ? ORDER BY created_at, id", (since,)
            ).fetchall()
        return [self._to_model(row) for row in rows]

    def needing_attention(self, since: str, limit: int = 100) -> list[Interaction]:
        """Questions the robot could not answer, or whose answer was marked unhelpful."""
        with self._db.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM interactions WHERE created_at >= ? AND resolved = 0 "
                "AND question <> '' AND (source IN ('fallback', 'gemini_no_info') OR feedback < 0) "
                "ORDER BY created_at DESC, id DESC LIMIT ?",
                (since, limit),
            ).fetchall()
        return [self._to_model(row) for row in rows]

    def delete_before(self, before: str) -> int:
        with self._db.connect() as connection:
            return connection.execute(
                "DELETE FROM interactions WHERE created_at < ?", (before,)
            ).rowcount

    def clear_text(self) -> int:
        """Erase stored question and answer text, keeping the anonymous counts."""
        with self._db.connect() as connection:
            return connection.execute(
                "UPDATE interactions SET question = '', answer = '' WHERE question <> '' OR answer <> ''"
            ).rowcount

    @staticmethod
    def _to_model(row: sqlite3.Row) -> Interaction:
        return Interaction(
            id=row["id"], created_at=row["created_at"], question=row["question"],
            answer=row["answer"], source=row["source"], language=row["language"],
            input_mode=row["input_mode"], ai_error=row["ai_error"],
            response_ms=row["response_ms"], feedback=row["feedback"],
            resolved=bool(row["resolved"]),
        )


class VisitRepository:
    def __init__(self, database: Database) -> None:
        self._db = database

    def add(
        self, started_at: str, duration_s: float, closest_m: float | None, engaged: bool,
        questions: int,
    ) -> None:
        with self._db.connect() as connection:
            connection.execute(
                "INSERT INTO visits (started_at, duration_s, closest_m, engaged, questions) "
                "VALUES (?, ?, ?, ?, ?)",
                (started_at, duration_s, closest_m, int(engaged), questions),
            )

    def summary_since(self, since: str) -> tuple[int, int, float]:
        """(visits, visits that reached conversation distance, average duration in seconds)."""
        with self._db.connect() as connection:
            row = connection.execute(
                "SELECT COUNT(*), COALESCE(SUM(engaged), 0), COALESCE(AVG(duration_s), 0) "
                "FROM visits WHERE started_at >= ?",
                (since,),
            ).fetchone()
        return int(row[0]), int(row[1]), float(row[2])

    def delete_before(self, before: str) -> int:
        with self._db.connect() as connection:
            return connection.execute("DELETE FROM visits WHERE started_at < ?", (before,)).rowcount


class ScheduleRepository:
    def __init__(self, database: Database) -> None:
        self._db = database

    def list(self) -> list[Schedule]:
        with self._db.connect() as connection:
            rows = connection.execute("SELECT * FROM schedules ORDER BY time_of_day, id").fetchall()
        return [
            Schedule(
                id=row["id"], title=row["title"], time_of_day=row["time_of_day"],
                weekdays=row["weekdays"], message=row["message"], media_id=row["media_id"],
                enabled=bool(row["enabled"]), last_run_date=row["last_run_date"],
            )
            for row in rows
        ]

    def save(
        self,
        schedule_id: int | None,
        title: str,
        time_of_day: str,
        weekdays: str,
        message: str,
        media_id: int | None,
        enabled: bool,
    ) -> int:
        now = _now()
        with self._db.connect() as connection:
            if schedule_id is None:
                cursor = connection.execute(
                    "INSERT INTO schedules (title, time_of_day, weekdays, message, media_id, "
                    "enabled, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (title, time_of_day, weekdays, message, media_id, int(enabled), now, now),
                )
                return cursor.lastrowid
            # Changing the time lets it run again today at the new time.
            connection.execute(
                "UPDATE schedules SET title = ?, weekdays = ?, message = ?, media_id = ?, "
                "enabled = ?, updated_at = ?, "
                "last_run_date = CASE WHEN time_of_day = ? THEN last_run_date ELSE '' END, "
                "time_of_day = ? WHERE id = ?",
                (title, weekdays, message, media_id, int(enabled), now, time_of_day, time_of_day,
                 schedule_id),
            )
            return schedule_id

    def mark_run(self, schedule_id: int, run_date: str) -> None:
        with self._db.connect() as connection:
            connection.execute(
                "UPDATE schedules SET last_run_date = ? WHERE id = ?", (run_date, schedule_id)
            )

    def delete(self, schedule_id: int) -> None:
        with self._db.connect() as connection:
            connection.execute("DELETE FROM schedules WHERE id = ?", (schedule_id,))


class ActivityLogRepository:
    def __init__(self, database: Database) -> None:
        self._db = database

    def add(self, actor: str, action: str, detail: str = "") -> None:
        with self._db.connect() as connection:
            connection.execute(
                "INSERT INTO activity_logs (created_at, actor, action, detail) VALUES (?, ?, ?, ?)",
                (_now(), actor, action, detail),
            )

    def recent(self, limit: int = 200) -> list[ActivityLog]:
        with self._db.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM activity_logs ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [
            ActivityLog(row["id"], row["created_at"], row["actor"], row["action"], row["detail"])
            for row in rows
        ]
