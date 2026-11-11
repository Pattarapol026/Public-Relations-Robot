"""SQLite connection handling, schema creation and backup."""
from __future__ import annotations

import logging
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterator

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 7

_SCHEMA = """
CREATE TABLE media_items (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    title         TEXT NOT NULL,
    description   TEXT NOT NULL DEFAULT '',
    category      TEXT NOT NULL DEFAULT '',
    media_type    TEXT NOT NULL CHECK (media_type IN ('video', 'image')),
    file_name     TEXT NOT NULL UNIQUE,
    original_name TEXT NOT NULL DEFAULT '',
    size_bytes    INTEGER NOT NULL DEFAULT 0,
    enabled       INTEGER NOT NULL DEFAULT 1,
    sort_order    INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);
CREATE TABLE announcements (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    title      TEXT NOT NULL,
    body       TEXT NOT NULL DEFAULT '',
    enabled    INTEGER NOT NULL DEFAULT 1,
    sort_order INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE knowledge_items (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    category         TEXT NOT NULL DEFAULT '',
    question         TEXT NOT NULL,
    answer           TEXT NOT NULL,
    keywords         TEXT NOT NULL DEFAULT '',
    enabled          INTEGER NOT NULL DEFAULT 1,
    show_as_shortcut INTEGER NOT NULL DEFAULT 0,
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL
);
CREATE TABLE app_settings (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE admin_users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);
CREATE TABLE activity_logs (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    actor      TEXT NOT NULL,
    action     TEXT NOT NULL,
    detail     TEXT NOT NULL DEFAULT ''
);
CREATE INDEX idx_activity_logs_created ON activity_logs (created_at);
"""

# Each entry upgrades the schema by one version: _MIGRATIONS[n] takes version n to n + 1.
_MIGRATIONS = {
    1: """
CREATE TABLE visual_triggers (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    kind          TEXT NOT NULL CHECK (kind IN ('card', 'gesture')),
    code          TEXT NOT NULL,
    title         TEXT NOT NULL,
    action        TEXT NOT NULL DEFAULT 'say'
                  CHECK (action IN ('say', 'feedback_up', 'feedback_down', 'stop')),
    response_text TEXT NOT NULL DEFAULT '',
    media_id      INTEGER REFERENCES media_items (id) ON DELETE SET NULL,
    enabled       INTEGER NOT NULL DEFAULT 1,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    UNIQUE (kind, code)
);
""",
    2: """
-- One row per question the robot answered. No name, face or voice is stored.
CREATE TABLE interactions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at  TEXT NOT NULL,              -- local time, ISO format
    question    TEXT NOT NULL DEFAULT '',   -- empty when question logging is off
    answer      TEXT NOT NULL DEFAULT '',
    source      TEXT NOT NULL,              -- knowledge / gemini / knowledge_offline / fallback
    language    TEXT NOT NULL DEFAULT 'th',
    input_mode  TEXT NOT NULL DEFAULT 'text',  -- voice / text / shortcut
    ai_error    TEXT NOT NULL DEFAULT '',
    response_ms INTEGER NOT NULL DEFAULT 0,
    feedback    INTEGER NOT NULL DEFAULT 0,  -- 1 helpful, -1 not, 0 not rated
    resolved    INTEGER NOT NULL DEFAULT 0   -- Admin has dealt with this unanswered question
);
CREATE INDEX idx_interactions_created ON interactions (created_at);

-- One row per person the camera saw come into the service area and leave again.
CREATE TABLE visits (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    duration_s REAL NOT NULL DEFAULT 0,
    closest_m  REAL,
    engaged    INTEGER NOT NULL DEFAULT 0,  -- came to conversation distance
    questions  INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX idx_visits_started ON visits (started_at);

-- Public relations the robot performs by itself at set times.
CREATE TABLE schedules (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    title         TEXT NOT NULL,
    time_of_day   TEXT NOT NULL,              -- HH:MM, local time
    weekdays      TEXT NOT NULL DEFAULT '0123456',  -- digits, 0 = Monday
    message       TEXT NOT NULL DEFAULT '',
    media_id      INTEGER REFERENCES media_items (id) ON DELETE SET NULL,
    enabled       INTEGER NOT NULL DEFAULT 1,
    last_run_date TEXT NOT NULL DEFAULT '',   -- YYYY-MM-DD of the last run, to run once a day
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);
""",
    3: """
-- Knowledge that stops being used after a date (YYYY-MM-DD), e.g. a temporary notice.
-- Empty means it never expires.
ALTER TABLE knowledge_items ADD COLUMN expires_on TEXT NOT NULL DEFAULT '';
""",
    4: """
-- The kind of place an entry belongs to (services/venues.py); '' = every kind.
-- Entries from before kinds of place existed were written for a college.
ALTER TABLE knowledge_items ADD COLUMN venue TEXT NOT NULL DEFAULT 'education';
""",
    5: """
-- Announcements are also told to the AI as facts, so they belong to a kind of place too.
ALTER TABLE announcements ADD COLUMN venue TEXT NOT NULL DEFAULT 'education';
""",
    6: """
-- The same entry in English, used when the visitor asks in English ('' = Thai only).
ALTER TABLE knowledge_items ADD COLUMN question_en TEXT NOT NULL DEFAULT '';
ALTER TABLE knowledge_items ADD COLUMN answer_en TEXT NOT NULL DEFAULT '';
""",
}


class Database:
    """Opens a short-lived connection per operation, so it is safe from any thread."""

    def __init__(self, path: Path) -> None:
        self._path = Path(path)

    @property
    def path(self) -> Path:
        return self._path

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self._path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            with connection:  # commit on success, roll back on error
                yield connection
        finally:
            connection.close()

    def initialize(self) -> None:
        """Create the schema on first run. Later versions add migrations here."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version > SCHEMA_VERSION:
                raise RuntimeError(
                    f"ฐานข้อมูลเป็นรุ่น {version} ซึ่งใหม่กว่าที่โปรแกรมรุ่นนี้รองรับ ({SCHEMA_VERSION})"
                )
            if version == 0:
                connection.executescript(_SCHEMA)
                version = 1
                logger.info("Database created at %s", self._path)
            while version < SCHEMA_VERSION:
                connection.executescript(_MIGRATIONS[version])
                version += 1
                logger.info("Database upgraded to schema version %d", version)
            connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    def backup(self, backup_dir: Path) -> Path:
        """Write a consistent copy of the database; safe while the app is running."""
        backup_dir.mkdir(parents=True, exist_ok=True)
        target = backup_dir / f"app-{datetime.now():%Y%m%d-%H%M%S}.db"
        source = sqlite3.connect(self._path, timeout=10)
        destination = sqlite3.connect(target)
        try:
            source.backup(destination)
        finally:
            destination.close()
            source.close()
        logger.info("Database backed up to %s", target)
        return target
