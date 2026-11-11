"""Settings, logging, state machine, admin auth and the conversation service."""
from __future__ import annotations

import json
import logging

import pytest

from admin.auth import AdminAuth, migrate_legacy_credentials
from app.logging_setup import SecretRedactionFilter
from app.state import ConversationState, StateManager, can_transition
from config.settings import ConfigError, load_settings
from database.models import KnowledgeItem
from database.repositories import AdminUserRepository
from services.conversation_service import FALLBACK_ANSWER, ConversationService

# --- settings ---


def test_defaults_without_env_give_demo_mode(settings):
    assert settings.demo_mode is True
    assert settings.robot_adapter == "mock"
    assert [action.id for action in settings.actions] == ["turn_left", "turn_right"]
    assert settings.max_video_bytes == 500 * 1_048_576


def test_api_key_is_loaded_but_never_printed():
    settings = load_settings(environ={"GEMINI_API_KEY": "secret-key-123"})
    assert settings.demo_mode is False
    assert "secret-key-123" not in repr(settings)


@pytest.mark.parametrize(
    "environ",
    [
        {"ROBOT_ADAPTER": "ros2"},
        {"LOG_LEVEL": "LOUD"},
        {"ROBOT_COMMAND_TIMEOUT_S": "abc"},
        {"ROBOT_COMMAND_TIMEOUT_S": "0"},
        {"UI_FULLSCREEN": "maybe"},
        {"MEDIA_MAX_VIDEO_MB": "-1"},
    ],
)
def test_invalid_env_values_are_rejected(environ):
    with pytest.raises(ConfigError):
        load_settings(environ=environ)


@pytest.mark.parametrize("bad_id", ["Lobby", "1st_floor", "lobby desk", "../x", ""])
def test_invalid_action_id_in_config_is_rejected(tmp_path, bad_id):
    config = {
        "institution_name": "x",
        "welcome_message": "x",
        "robot": {"actions": [{"id": bad_id, "label": "x"}]},
    }
    path = tmp_path / "app_config.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    with pytest.raises(ConfigError):
        load_settings(config_path=path, environ={})


def test_missing_config_file_is_reported(tmp_path):
    with pytest.raises(ConfigError):
        load_settings(config_path=tmp_path / "missing.json", environ={})


# --- logging ---


def test_secret_is_redacted_from_log_records():
    record = logging.LogRecord(
        "test", logging.INFO, __file__, 1, "calling api with key=%s", ("secret-key-123",), None
    )
    SecretRedactionFilter(["secret-key-123", ""]).filter(record)
    assert record.getMessage() == "calling api with key=***"


# --- state machine ---


def test_normal_turn_emits_each_transition():
    manager = StateManager()
    seen = []
    manager.state_changed.connect(lambda old, new: seen.append((old, new)))
    S = ConversationState
    for state in (S.LISTENING, S.THINKING, S.SPEAKING, S.IDLE):
        assert manager.transition(state)
    assert seen == [
        (S.IDLE, S.LISTENING), (S.LISTENING, S.THINKING),
        (S.THINKING, S.SPEAKING), (S.SPEAKING, S.IDLE),
    ]


def test_invalid_transition_is_refused_and_state_is_kept():
    manager = StateManager()
    assert manager.transition(ConversationState.ERROR) is False
    assert manager.state is ConversationState.IDLE
    assert not can_transition(ConversationState.ERROR, ConversationState.SPEAKING)


def test_reset_returns_to_idle_from_any_state():
    manager = StateManager()
    manager.transition(ConversationState.THINKING)
    manager.transition(ConversationState.ERROR)
    manager.reset()
    assert manager.state is ConversationState.IDLE


# --- admin auth ---


class MemoryStore:
    def __init__(self, record=None) -> None:
        self.record = record

    def load(self):
        return self.record

    def save(self, record) -> None:
        self.record = record


def test_password_is_stored_hashed_in_database_and_verifies(database):
    auth = AdminAuth(AdminUserRepository(database))
    assert auth.is_configured() is False
    assert auth.verify("anything") is False

    auth.set_password("correct horse")
    assert auth.is_configured()
    with database.connect() as connection:
        stored = connection.execute("SELECT password_hash FROM admin_users").fetchone()[0]
    assert "correct horse" not in stored
    assert auth.verify("correct horse") is True
    assert auth.verify("wrong horse") is False
    # A fresh instance (app restart) reads the same credential.
    assert AdminAuth(AdminUserRepository(database)).verify("correct horse") is True


def test_short_password_is_refused():
    store = MemoryStore()
    with pytest.raises(ValueError):
        AdminAuth(store).set_password("short")
    assert store.record is None


def test_lockout_after_repeated_failures():
    now = [1000.0]
    auth = AdminAuth(MemoryStore(), max_attempts=3, lockout_s=60, clock=lambda: now[0])
    auth.set_password("correct horse")
    for _ in range(3):
        assert auth.verify("nope") is False
    assert auth.locked_remaining() == 60
    assert auth.verify("correct horse") is False  # locked even with the right password
    now[0] += 61
    assert auth.verify("correct horse") is True


@pytest.mark.parametrize(
    "record", [None, "text", {}, {"algorithm": "plain"}, {"algorithm": "scrypt", "hash": "zz"}]
)
def test_malformed_credential_record_never_grants_access(record):
    assert AdminAuth(MemoryStore(record)).verify("") is False


def test_v01_credential_file_is_migrated_once(tmp_path, database):
    legacy_store = MemoryStore()
    AdminAuth(legacy_store).set_password("correct horse")
    legacy_path = tmp_path / "admin_credentials.json"
    legacy_path.write_text(json.dumps(legacy_store.record), encoding="utf-8")

    repository = AdminUserRepository(database)
    assert migrate_legacy_credentials(legacy_path, repository) is True
    assert not legacy_path.exists()
    assert AdminAuth(repository).verify("correct horse") is True
    assert migrate_legacy_credentials(legacy_path, repository) is False


def test_migration_does_not_overwrite_an_existing_database_password(tmp_path, database):
    repository = AdminUserRepository(database)
    AdminAuth(repository).set_password("database password")
    old_store = MemoryStore()
    AdminAuth(old_store).set_password("old file password")
    legacy_path = tmp_path / "admin_credentials.json"
    legacy_path.write_text(json.dumps(old_store.record), encoding="utf-8")

    migrate_legacy_credentials(legacy_path, repository)
    assert AdminAuth(repository).verify("database password") is True
    assert AdminAuth(repository).verify("old file password") is False


# --- conversation ---


def _item(item_id, question, answer, keywords=(), enabled=True) -> KnowledgeItem:
    return KnowledgeItem(item_id, "FAQ", question, answer, tuple(keywords), enabled, True)


KNOWLEDGE = [
    _item(1, "เวลาทำการ", "เปิด 8 โมง", ["เวลา", "กี่โมง"]),
    _item(2, "สมัครเรียนได้ที่ไหน", "ที่งานทะเบียน", ["สมัคร", "ทะเบียน"]),
    _item(3, "ข้อมูลที่ปิดไว้", "ไม่ควรตอบ", ["ลับ"], enabled=False),
]


def test_exact_question_and_keyword_match():
    service = ConversationService(lambda: KNOWLEDGE)
    exact = service.respond("  เวลาทำการ ")
    assert (exact.answer_text, exact.source) == ("เปิด 8 โมง", "knowledge")
    assert service.respond("อยากสมัครเรียนต้องทำยังไง").answer_text == "ที่งานทะเบียน"


def test_unknown_or_disabled_knowledge_gets_fallback_without_intent():
    service = ConversationService(lambda: KNOWLEDGE)
    for question in ("อยากรู้ความหมายของชีวิต", "ขอข้อมูลลับ", "ข้อมูลที่ปิดไว้"):
        result = service.respond(question)
        assert (result.answer_text, result.source) == (FALLBACK_ANSWER, "fallback")
        assert result.suggested_intent is None


def test_knowledge_is_read_fresh_for_every_question():
    knowledge = []
    service = ConversationService(lambda: knowledge)
    assert service.respond("โรงอาหาร").source == "fallback"
    knowledge.append(_item(9, "โรงอาหารอยู่ไหน", "หลังอาคาร 2", ["โรงอาหาร"]))
    assert service.respond("โรงอาหาร").answer_text == "หลังอาคาร 2"
