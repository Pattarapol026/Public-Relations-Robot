"""Listening only to the person in front of the camera (9 Nov 2026): nobody there = the sound is ignored; a face clearly
seen with a mouth that does not move while there is speech = not them; anything the camera cannot tell = heard as before."""
from __future__ import annotations

import time

import numpy as np
import pytest
from PySide6.QtWidgets import QApplication

from app.application import Application
from app.state import ConversationState
from app.voice_controller import VoiceStatus
from config.settings import load_settings
from fakes import LOUD, QUIET, FakeMicrophone, FakeStt
from ui.log_bridge import QtLogHandler
from vision.mock_source import MockVisionSource
from vision.mouth import HEAD_MIN_SIDE, MIN_FACE_READINGS, MouthTracker, head_part


@pytest.fixture(scope="module")
def qt_app():
    return QApplication.instance() or QApplication([])


def wait_until(qt_app, condition, timeout_s: float = 4.0) -> None:
    deadline = time.monotonic() + timeout_s
    while not condition():
        assert time.monotonic() < deadline, "condition not reached in time"
        qt_app.processEvents()
        time.sleep(0.01)


def pump(qt_app, seconds: float) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        qt_app.processEvents()
        time.sleep(0.01)


# --- the readings ---


def test_the_mouth_moved_only_when_the_jaw_opening_varied_enough():
    tracker = MouthTracker()
    for index in range(8):  # talking: the jaw goes up and down
        tracker.add(10.0 + index * 0.1, 0.08 + 0.3 * (index % 3) / 2)
    assert tracker.activity(10.0, 11.0, 0.10) is True
    still = MouthTracker()
    for index in range(8):  # a face, mouth shut (the reading only jitters)
        still.add(10.0 + index * 0.1, 0.12 + 0.01 * (index % 2))
    assert still.activity(10.0, 11.0, 0.10) is False


def test_without_enough_of_a_face_there_is_no_opinion_never_a_no():
    tracker = MouthTracker()
    for index in range(10):
        tracker.add(10.0 + index * 0.1, None)  # no face in any picture
    assert tracker.activity(10.0, 11.0, 0.10) is None and not tracker.face_seen(11.0)
    few = MouthTracker()
    for index in range(MIN_FACE_READINGS - 1):
        few.add(10.0 + index * 0.1, 0.3 * (index % 2))
    assert few.activity(10.0, 11.0, 0.10) is None  # two readings are not evidence
    assert MouthTracker().activity(0.0, 5.0, 0.1) is None


def test_readings_older_than_a_few_seconds_are_forgotten_and_a_stretch_only_counts_its_own_readings():
    tracker = MouthTracker()
    for index in range(6):
        tracker.add(1.0 + index * 0.1, 0.1 + 0.3 * (index % 2))  # talking, long ago
    tracker.add(30.0, 0.1)
    assert tracker.activity(0.0, 31.0, 0.1) is None  # the old ones are gone
    for index in range(6):
        tracker.add(31.0 + index * 0.1, 0.12)
    assert tracker.activity(31.0, 32.0, 0.1) is False and tracker.face_seen(32.0)


def test_the_head_is_cut_out_of_a_persons_box_and_enlarged_to_a_size_a_face_can_be_found_in():
    import cv2

    picture = np.zeros((480, 640, 3), dtype=np.uint8)
    head = head_part(picture, (0.40, 0.20, 0.60, 0.95), cv2)  # a person 128 px wide
    assert head is not None and min(head.shape[:2]) >= HEAD_MIN_SIDE
    assert head_part(picture, (0.5, 0.5, 0.505, 0.505), cv2) is None  # a speck is not a person


# --- the camera source and the controller ---


@pytest.fixture
def app_with_camera(qt_app, tmp_path):
    camera, microphone = MockVisionSource(), FakeMicrophone()
    settings = load_settings(environ={
        "DATA_DIR": str(tmp_path / "data"), "LOG_DIR": str(tmp_path / "logs"), "MEDIA_DIR": str(tmp_path / "media"),
        "STT_ENABLED": "true", "TTS_ENABLED": "false", "TTS_ENGINE": "local",
        "MIC_SILENCE_SECONDS": "0.4", "MIC_NO_SPEECH_TIMEOUT_S": "3", "MIC_MAX_SECONDS": "6",
        "VISION_GREET_DWELL_SECONDS": "0.1", "VISION_DWELL_SECONDS": "0.1", "VISION_LEAVE_SECONDS": "10",
    })
    application = Application(settings, QtLogHandler(), microphone=microphone, stt=FakeStt(), vision_source=camera)
    wait_until(qt_app, lambda: application.voice.status is VoiceStatus.READY)
    yield application, camera, microphone
    application.shutdown()
    application.window.deleteLater()


def test_the_controller_tells_talking_from_a_shut_mouth_from_no_face(qt_app, app_with_camera):
    application, camera, _microphone = app_with_camera
    camera.place_person(1.5)
    wait_until(qt_app, lambda: application.vision.present)
    for mode, expected in (("talking", True), ("still", False), (None, None)):
        camera.set_mouth(mode)
        pump(qt_app, 1.2)
        assert application.vision.mouth_activity(time.monotonic() - 1.0, 0.10) is expected, mode


# --- the rule in the application ---


def test_sound_with_nobody_in_front_of_a_working_camera_is_not_from_the_visitor(qt_app, app_with_camera):
    application, camera, _microphone = app_with_camera
    now = time.monotonic()
    pump(qt_app, 0.3)
    assert application._visitor_gate(now - 0.2, now) is False  # the camera works and sees no one
    camera.place_person(1.5)
    wait_until(qt_app, lambda: application.vision.present)
    assert application._visitor_gate(now - 0.2, now) is None  # someone is there, too soon to look at the mouth


def test_a_still_mouth_is_not_the_visitor_only_after_the_grace_and_only_when_a_face_is_seen(qt_app, app_with_camera):
    application, camera, _microphone = app_with_camera
    camera.place_person(1.5)
    wait_until(qt_app, lambda: application.vision.present)
    camera.set_mouth("still")
    pump(qt_app, 2.0)
    now = time.monotonic()
    assert application._visitor_gate(now - 0.5, now) is None  # not yet: 0.5 s of sound is too little to judge
    assert application._visitor_gate(now - 2.0, now) is False  # 2 s of sound, a face seen, the mouth still
    camera.set_mouth("talking")
    pump(qt_app, 1.5)
    now = time.monotonic()
    assert application._visitor_gate(now - 2.0, now) is True  # the mouth moves: it is them
    camera.set_mouth(None)  # turned away: no face
    pump(qt_app, 4.0)
    now = time.monotonic()
    assert application._visitor_gate(now - 2.0, now) is None  # cannot tell: heard, as before


def test_the_admin_can_switch_it_off_and_a_camera_that_is_not_running_never_gates(qt_app, app_with_camera):
    application, camera, _microphone = app_with_camera
    now = time.monotonic()
    pump(qt_app, 0.3)
    assert application._visitor_gate(now - 3.0, now) is False
    import dataclasses

    application.tuning.save(dataclasses.replace(application.tuning.values, voice_visitor_only=False))
    assert application._visitor_gate(now - 3.0, now) is None  # switched off in the Admin
    application.tuning.save(dataclasses.replace(application.tuning.values, voice_visitor_only=True, voice_lip_check=False))
    camera.place_person(1.5)
    wait_until(qt_app, lambda: application.vision.present)
    camera.set_mouth("still")
    pump(qt_app, 2.0)
    now = time.monotonic()
    assert application._visitor_gate(now - 2.0, now) is None  # the lip check is off: presence alone
    camera.stop()
    pump(qt_app, 0.3)
    application.tuning.save(dataclasses.replace(application.tuning.values, voice_lip_check=True))
    assert application._visitor_gate(now - 2.0, now) is None  # no camera: nothing to judge by


# --- the gate inside the voice controller ---


def test_a_sound_from_nowhere_starts_no_turn_and_a_sound_that_may_be_the_visitors_does(qt_app, app_with_camera):
    application, camera, microphone = app_with_camera
    verdict = {"now": False}
    application.voice.visitor_gate = lambda since, now: verdict["now"]
    application.window.user_screen._on_talk_button()  # (a button press bypasses the gate: use the hands-free start instead)
    application.conversation.stop()
    application.voice.clear_gate_bypass()
    assert application.voice.start_listening()
    microphone.level = LOUD
    pump(qt_app, 1.2)
    assert application.voice.is_listening and not application.voice.heard_speech  # ignored: no turn began
    verdict["now"] = None  # now it could be them
    wait_until(qt_app, lambda: application.voice.heard_speech, timeout_s=3.0)


def test_pressing_start_makes_the_robot_hear_whatever_the_camera_sees(qt_app, app_with_camera):
    application, camera, microphone = app_with_camera
    application.voice.visitor_gate = lambda since, now: False  # the camera says: nobody there (it points at the ceiling)
    application.window.user_screen._on_talk_button()  # "เริ่มสนทนา"
    assert application.voice.gate_bypassed
    microphone.level = LOUD
    wait_until(qt_app, lambda: application.voice.heard_speech, timeout_s=3.0)
    microphone.level = QUIET
    wait_until(qt_app, lambda: application.conversation.state.state is ConversationState.SPEAKING, timeout_s=6.0)
    assert application.voice.gate_bypassed  # the answer renewed it: the conversation goes on
    application.window.user_screen._stop_conversation()
    assert not application.voice.gate_bypassed


def test_a_turn_that_was_not_the_visitors_is_dropped_at_its_end_and_a_short_one_never_is(qt_app, app_with_camera):
    application, camera, microphone = app_with_camera
    stt = application.voice._stt
    verdict = {"now": None}
    application.voice.visitor_gate = lambda since, now: verdict["now"]  # nothing wrong while it goes on ...
    assert application.voice.start_listening()
    microphone.level = LOUD
    pump(qt_app, 1.3)  # more than a second of speech
    verdict["now"] = False  # ... but the camera, looking back over the whole turn, says the mouth never moved
    microphone.level = QUIET
    wait_until(qt_app, lambda: stt.transcribed == 1, timeout_s=6.0)
    wait_until(qt_app, lambda: application.conversation.state.state is ConversationState.IDLE, timeout_s=6.0)
    assert application.window.user_screen.transcript_texts() == []  # transcribed, and not answered

    application.conversation.stop()
    verdict["now"] = None
    assert application.voice.start_listening()
    microphone.level = LOUD
    pump(qt_app, 0.6)  # a short turn: too little to judge by the mouth
    verdict["now"] = False  # (the camera would say no, at the end)
    microphone.level = QUIET
    wait_until(qt_app, lambda: len(application.window.user_screen.transcript_texts()) >= 2, timeout_s=6.0)
