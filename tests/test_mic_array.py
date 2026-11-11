"""The microphone chip says "ready" only while the microphone array (XVF3800) is plugged in."""
from __future__ import annotations

import sys
import time

import pytest
from PySide6.QtWidgets import QApplication

from app import mic_array_controller
from app.application import Application
from app.voice_controller import VoiceStatus
from audio import mic_array
from audio.speech_to_text import SpeechToTextError
from config.settings import load_settings
from fakes import FakeMicrophone, FakeStt
from ui.fonts import apply_app_font
from ui.log_bridge import QtLogHandler

BOARD = "reSpeaker XVF3800 4-Mic Array"


@pytest.fixture(scope="module")
def qt_app():
    qt_app = QApplication.instance() or QApplication([])
    apply_app_font(qt_app)
    return qt_app


def wait_until(qt_app, condition, timeout_s: float = 5.0) -> None:
    deadline = time.monotonic() + timeout_s
    while not condition():
        assert time.monotonic() < deadline, "condition not reached in time"
        qt_app.processEvents()
        time.sleep(0.01)


class Plug:
    """Stands in for the USB port: ``board`` is the name while it is plugged in, else None."""

    def __init__(self, board: str | None = None) -> None:
        self.board = board

    def __call__(self) -> str | None:
        return self.board


def build(qt_app, tmp_path, plug, monkeypatch, **environ):
    monkeypatch.setattr(mic_array_controller, "POLL_MS", 50)  # plugging shows in 50 ms here, 3 s for real
    settings = load_settings(environ={
        "DATA_DIR": str(tmp_path / "data"), "LOG_DIR": str(tmp_path / "logs"),
        "MEDIA_DIR": str(tmp_path / "media"), "STT_ENABLED": "true", "TTS_ENABLED": "false",
        "VISION_ENABLED": "false", **environ,
    })
    application = Application(
        settings, QtLogHandler(), microphone=FakeMicrophone(), stt=FakeStt(), mic_array_detector=plug,
    )
    wait_until(qt_app, lambda: application.voice.status is VoiceStatus.READY)
    return application


def chip(application) -> str:
    return application.window.user_screen._voice_chip.text()


def chip_state(application) -> str:
    return application.window.user_screen._voice_chip.property("state")


def test_the_chip_follows_the_array_being_plugged_in_and_out(qt_app, tmp_path, monkeypatch):
    plug = Plug(None)
    application = build(qt_app, tmp_path, plug, monkeypatch)
    try:
        # Working microphone, but not the array: not ready (red).
        wait_until(qt_app, lambda: chip(application) == "ไมโครโฟน: ไม่พร้อม")
        assert chip_state(application) == "error"
        assert "XVF3800" in application.window.user_screen._voice_chip.toolTip()

        plug.board = BOARD  # plugged in: no restart needed
        wait_until(qt_app, lambda: chip(application) == "ไมโครโฟน: พร้อม")
        assert chip_state(application) == "idle"
        assert BOARD in application.window.user_screen._voice_chip.toolTip()

        plug.board = None  # unplugged again
        wait_until(qt_app, lambda: chip(application) == "ไมโครโฟน: ไม่พร้อม")
    finally:
        application.shutdown()
        application.window.deleteLater()


def test_a_microphone_that_cannot_be_used_still_says_so_with_the_array_plugged_in(qt_app, tmp_path, monkeypatch):
    monkeypatch.setattr(mic_array_controller, "POLL_MS", 50)
    stt = FakeStt()
    stt.load_error = SpeechToTextError("โหลดโมเดลถอดเสียงไม่สำเร็จ")
    settings = load_settings(environ={
        "DATA_DIR": str(tmp_path / "data"), "LOG_DIR": str(tmp_path / "logs"),
        "MEDIA_DIR": str(tmp_path / "media"), "STT_ENABLED": "true", "TTS_ENABLED": "false",
        "VISION_ENABLED": "false",
    })
    application = Application(
        settings, QtLogHandler(), microphone=FakeMicrophone(), stt=stt, mic_array_detector=Plug(BOARD),
    )
    try:
        wait_until(qt_app, lambda: application.voice.status is VoiceStatus.UNAVAILABLE)
        wait_until(qt_app, lambda: application.mic_array.connected)
        assert "ใช้ไม่ได้" in chip(application)  # the array being there does not make a broken input ready
        assert chip_state(application) == "error"
    finally:
        application.shutdown()
        application.window.deleteLater()


def test_the_array_can_be_made_optional(qt_app, tmp_path, monkeypatch):
    application = build(qt_app, tmp_path, Plug(None), monkeypatch, MIC_ARRAY_REQUIRED="false")
    try:
        assert application.mic_array is None
        assert chip(application) == "ไมโครโฟน: พร้อม"  # any working microphone, as before
    finally:
        application.shutdown()
        application.window.deleteLater()


def test_a_detector_that_fails_counts_as_not_connected(qt_app, tmp_path, monkeypatch):
    def broken():
        raise OSError("device list unreadable")

    application = build(qt_app, tmp_path, broken, monkeypatch)
    try:
        wait_until(qt_app, lambda: chip(application) == "ไมโครโฟน: ไม่พร้อม")
    finally:
        application.shutdown()
        application.window.deleteLater()


def test_nothing_is_found_when_no_such_device_is_present():
    assert mic_array.find_device(("0000",), ("no-such-device-name",)) is None


@pytest.mark.skipif(sys.platform != "win32", reason="reads Windows' device list")
def test_a_present_device_is_found_by_name_and_by_vendor():
    # Every Windows PC with USB has a root hub in the list; this proves the list is read.
    found = mic_array.find_device((), ("usb",))
    assert found is not None
