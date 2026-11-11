from __future__ import annotations

import os

# Must be set before any QApplication is created so tests need no display.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from config.settings import Settings, load_settings
from database.db import Database


@pytest.fixture
def settings(tmp_path) -> Settings:
    return load_settings(
        environ={
            "DATA_DIR": str(tmp_path / "data"),
            "LOG_DIR": str(tmp_path / "logs"),
            "MEDIA_DIR": str(tmp_path / "media"),
            "ROBOT_COMMAND_TIMEOUT_S": "5",
            # Tests never touch the real microphone or download a speech model.
            "STT_ENABLED": "false",
            # ...nor the real camera.
            "VISION_ENABLED": "false",
            # ...nor load a speech model or play sound.
            "TTS_ENABLED": "false",
            "TTS_ENGINE": "local",  # ...and never reach the online voice
        }
    )


@pytest.fixture
def database(tmp_path) -> Database:
    database = Database(tmp_path / "data" / "app.db")
    database.initialize()
    return database


@pytest.fixture(autouse=True)
def _no_always_on_listening(monkeypatch):
    """Always-on listening (the idle robot reopens the microphone by itself) would race every test
    that scripts the microphone by hand. It has its own test, which turns the timer on itself."""
    monkeypatch.setattr("ui.main_window.LISTEN_AGAIN_MS", 10**9)
