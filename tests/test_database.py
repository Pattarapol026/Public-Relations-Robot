from __future__ import annotations

import sqlite3

from database.db import SCHEMA_VERSION, Database
from database.repositories import (
    MOVE_DOWN,
    MOVE_UP,
    ActivityLogRepository,
    AnnouncementRepository,
    KnowledgeRepository,
    MediaRepository,
    SettingsRepository,
)

REQUIRED_TABLES = {
    "media_items", "announcements", "knowledge_items",
    "app_settings", "admin_users", "activity_logs",
}


def test_first_run_creates_all_tables_and_is_repeatable(tmp_path):
    database = Database(tmp_path / "nested" / "app.db")
    database.initialize()
    database.initialize()  # second start must not fail or recreate
    with database.connect() as connection:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master")}
        version = connection.execute("PRAGMA user_version").fetchone()[0]
    assert REQUIRED_TABLES <= tables
    assert version == SCHEMA_VERSION


def test_failed_transaction_is_rolled_back(database):
    repository = AnnouncementRepository(database)
    try:
        with database.connect() as connection:
            connection.execute(
                "INSERT INTO announcements (title, created_at, updated_at) VALUES ('x', 'a', 'b')"
            )
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    assert repository.list() == []


def test_backup_is_a_readable_copy(database, tmp_path):
    AnnouncementRepository(database).add("ประกาศ", "รายละเอียด")
    target = database.backup(tmp_path / "backups")
    copy = sqlite3.connect(target)
    try:
        assert copy.execute("SELECT title FROM announcements").fetchone()[0] == "ประกาศ"
    finally:
        copy.close()


def test_announcement_crud_and_ordering(database):
    repository = AnnouncementRepository(database)
    first = repository.add("หนึ่ง", "")
    second = repository.add("สอง", "", enabled=False)
    third = repository.add("สาม", "")
    assert [item.title for item in repository.list()] == ["หนึ่ง", "สอง", "สาม"]
    assert [item.title for item in repository.list(enabled_only=True)] == ["หนึ่ง", "สาม"]

    assert repository.move(third, MOVE_UP) is True
    assert [item.title for item in repository.list()] == ["หนึ่ง", "สาม", "สอง"]
    assert repository.move(first, MOVE_UP) is False  # already first
    assert repository.move(second, MOVE_DOWN) is False  # already last
    assert repository.move(9999, MOVE_UP) is False

    repository.update(first, "หนึ่ง (แก้)", "เนื้อหา", False)
    repository.delete(third)
    items = repository.list()
    assert [(item.title, item.body, item.enabled) for item in items] == [
        ("หนึ่ง (แก้)", "เนื้อหา", False), ("สอง", "", False),
    ]


def test_knowledge_keywords_round_trip(database):
    repository = KnowledgeRepository(database)
    item_id = repository.add("FAQ", "ถาม", "ตอบ", [" เวลา ", "", "กี่โมง"], show_as_shortcut=True)
    item = repository.list()[0]
    assert item.keywords == ("เวลา", "กี่โมง")
    assert item.show_as_shortcut is True

    repository.update(item_id, "ติดต่อ", "ถามใหม่", "ตอบใหม่", ["โทร"], False, False)
    item = repository.list()[0]
    assert (item.category, item.question, item.keywords, item.enabled) == (
        "ติดต่อ", "ถามใหม่", ("โทร",), False,
    )
    assert repository.list(enabled_only=True) == []
    repository.delete(item_id)
    assert repository.list() == []


def test_values_are_bound_not_interpolated(database):
    """Text that looks like SQL is stored as text."""
    repository = AnnouncementRepository(database)
    hostile = "x'); DROP TABLE announcements; --"
    repository.add(hostile, hostile)
    assert repository.list()[0].title == hostile


def test_media_repository_crud(database):
    repository = MediaRepository(database)
    item = repository.add("วิดีโอ", "คำอธิบาย", "แนะนำ", "video", "a" * 32 + ".mp4", "intro.mp4", 123)
    assert (item.enabled, item.sort_order, item.size_bytes) == (True, 1, 123)
    assert repository.get(item.id) == item
    repository.update(item.id, "ชื่อใหม่", "", "", False)
    assert repository.get(item.id).title == "ชื่อใหม่"
    assert repository.list(enabled_only=True) == []
    repository.delete(item.id)
    assert repository.get(item.id) is None


def test_settings_upsert_and_activity_log(database):
    settings = SettingsRepository(database)
    assert settings.get("missing") is None
    assert settings.get("missing", "default") == "default"
    settings.set("welcome", "สวัสดี")
    settings.set("welcome", "ยินดีต้อนรับ")
    assert settings.get("welcome") == "ยินดีต้อนรับ"

    activity = ActivityLogRepository(database)
    activity.add("admin", "media.add", "id=1")
    activity.add("touch", "robot.stop")
    entries = activity.recent()
    assert [(e.actor, e.action, e.detail) for e in entries] == [
        ("touch", "robot.stop", ""), ("admin", "media.add", "id=1"),
    ]
    assert len(activity.recent(limit=1)) == 1
