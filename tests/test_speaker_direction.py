"""Voice direction (DOA) tracking and speaker matching, with scripted readings.

No real direction-capable microphone exists on the test machine: these tests
check the logic with readings whose true direction is known. Real accuracy has
to be measured with the device (left, right and front of it).
"""
from __future__ import annotations

import math
import time

import pytest
from PySide6.QtWidgets import QApplication

from app.application import Application
from app.state import ConversationState
from app.voice_controller import VoiceStatus
from audio.direction import (
    DirectionReading,
    DirectionSource,
    DirectionTracker,
    TrackerConfig,
    angle_between,
    wrap_degrees,
)
from config.settings import load_settings
from fakes import LOUD, QUIET, FakeMicrophone, FakeStt
from services.speaker_matching import (
    MatchConfig,
    MatchStatus,
    MountGeometry,
    match_speaker,
    person_azimuth,
)
from ui.log_bridge import QtLogHandler
from vision.interfaces import PersonObservation, SourceStatus
from vision.mock_source import MockVisionSource

GEOMETRY = MountGeometry()
MATCH = MatchConfig(tolerance_deg=15.0, margin_deg=10.0)


def readings(*angles: float, confidence: float = 0.9, start: float = 100.0) -> list[DirectionReading]:
    return [DirectionReading(start + i * 0.1, angle, confidence) for i, angle in enumerate(angles)]


def tracked(*angles: float, **kwargs) -> DirectionTracker:
    tracker = DirectionTracker(TrackerConfig(window_s=10.0))
    for reading in readings(*angles, **kwargs):
        tracker.add(reading, speech_active=True, robot_speaking=False)
    return tracker


def person(x: float, z: float, y: float | None = None) -> PersonObservation:
    return PersonObservation(x_m=x, z_m=z, confidence=0.9, y_m=y)


def close_enough(p: PersonObservation) -> bool:
    return 0.3 <= p.z_m <= 1.5


# --- angles ---


def test_angles_wrap_and_compare_across_the_back():
    assert wrap_degrees(190) == -170 and wrap_degrees(-180) == 180 and wrap_degrees(540) == 180
    assert angle_between(175, -175) == pytest.approx(10)


# --- tracker: when readings count ---


def test_readings_count_only_while_someone_speaks_and_the_robot_is_silent():
    tracker = DirectionTracker(TrackerConfig(window_s=10.0))
    for reading in readings(30, 30, 30):
        tracker.add(reading, speech_active=True, robot_speaking=True)  # the robot's own voice
    for reading in readings(-60, -60, -60, start=101):
        tracker.add(reading, speech_active=False, robot_speaking=False)  # room noise between words
    assert tracker.estimate(now=102) is None
    assert (tracker.ignored_while_robot_spoke, tracker.ignored_without_speech) == (3, 3)


def test_steady_voice_gives_a_confident_direction():
    estimate = tracked(-31, -29, -30, -32, -28).estimate(now=101)
    assert estimate.azimuth_deg == pytest.approx(-30, abs=0.5)
    assert estimate.stable and estimate.confidence > 0.8 and estimate.readings == 5


def test_mean_direction_is_right_behind_the_microphone_not_zero():
    estimate = tracked(178, -178, 179, -179).estimate(now=101)
    assert angle_between(estimate.azimuth_deg, 180) < 1.5


def test_a_stray_reading_is_dropped_and_a_wandering_one_is_not_relied_on():
    estimate = tracked(10, 12, 11, 9, 120).estimate(now=101)  # a door slams to the side
    assert estimate.azimuth_deg == pytest.approx(10.5, abs=1) and estimate.readings == 4
    assert estimate.confidence < 0.9  # one in five disagreed
    wandering = tracked(-30, 0, 30, -20, 20).estimate(now=101)
    assert wandering is not None and not wandering.stable


def test_doubtful_and_old_readings_do_not_count():
    assert tracked(20, 20, 20, confidence=0.1).estimate(now=101) is None
    tracker = DirectionTracker(TrackerConfig(window_s=1.0))
    for reading in readings(20, 20, 20):
        tracker.add(reading, True, False)
    assert tracker.estimate(now=100.3) is not None
    assert tracker.estimate(now=105.0) is None  # the speaker has stopped


# --- matching a direction to the people the camera sees ---


def estimate_at(angle: float):
    return tracked(angle, angle, angle, angle).estimate(now=101)


def test_left_right_and_front_speakers_are_told_apart():
    # All inside the camera's view (±34.5°): -31°, 0°, +31°.
    left, front, right = person(-0.6, 1.0, y=0.1), person(0.0, 1.2), person(0.6, 1.0)
    people = [left, front, right]
    for target in people:
        result = match_speaker(estimate_at(person_azimuth(target, GEOMETRY)), people, GEOMETRY, MATCH, close_enough)
        assert result.status is MatchStatus.MATCHED and result.person == target
        assert result.error_deg == pytest.approx(0, abs=0.5) and result.from_visitor is True
    assert person_azimuth(left, GEOMETRY) == pytest.approx(math.degrees(math.atan2(-0.6, 1.0)))


def test_microphone_offset_and_turn_are_taken_into_account():
    # Microphone 20 cm to the camera's right, turned 10° further right.
    geometry = MountGeometry(mic_x_m=0.2, mic_yaw_deg=10.0)
    visitor = person(0.2, 1.0)  # straight in front of the microphone's position
    assert person_azimuth(visitor, geometry) == pytest.approx(-10.0)
    result = match_speaker(estimate_at(-10.0), [visitor, person(-0.8, 1.0)], geometry, MATCH, close_enough)
    assert result.status is MatchStatus.MATCHED and result.person == visitor


def test_people_at_similar_angles_are_not_guessed():
    near, behind = person(0.10, 1.0), person(0.25, 2.5)  # 5.7° and 5.7° — the same direction
    result = match_speaker(estimate_at(6.0), [near, behind], GEOMETRY, MATCH, close_enough)
    assert result.status is MatchStatus.AMBIGUOUS and result.person is None and result.from_visitor is None


def test_voice_from_where_nobody_stands_is_not_the_visitor():
    result = match_speaker(estimate_at(25.0), [person(-0.5, 1.0)], GEOMETRY, MATCH, close_enough)
    assert result.status is MatchStatus.NO_PERSON and result.from_visitor is False


def test_far_speaker_is_matched_but_is_not_talking_to_the_robot():
    result = match_speaker(estimate_at(0.0), [person(0.0, 3.0)], GEOMETRY, MATCH, close_enough)
    assert result.status is MatchStatus.MATCHED and result.from_visitor is False


@pytest.mark.parametrize(
    "estimate, people, camera_ready, status",
    [
        (None, [person(0, 1)], True, MatchStatus.NO_DIRECTION),
        ("unsteady", [person(0, 1)], True, MatchStatus.UNSTEADY),
        (80.0, [person(0, 1)], True, MatchStatus.OUT_OF_VIEW),  # beyond the camera's ±34.5°
        (0.0, [PersonObservation(0.0, 0.0, 0.9)], True, MatchStatus.UNMEASURED),
        (0.0, [], False, MatchStatus.NO_CAMERA),  # no camera: "nobody seen" means "unknown"
    ],
)
def test_cases_that_must_not_decide_anything(estimate, people, camera_ready, status):
    if estimate == "unsteady":
        estimate = tracked(-40, 0, 40, -30, 30).estimate(now=101)
    elif estimate is not None:
        estimate = estimate_at(estimate)
    result = match_speaker(estimate, people, GEOMETRY, MATCH, close_enough, camera_ready=camera_ready)
    assert result.status is status and result.person is None and result.from_visitor is None


def test_camera_height_is_kept_when_the_camera_reports_it():
    assert PersonObservation(0.1, 1.0, 0.9, None, -0.25).y_m == -0.25
    assert PersonObservation(0.1, 1.0, 0.9).y_m is None  # unknown, not zero


# --- in the application ---


class ScriptedDirection(DirectionSource):
    """A stand-in for a direction-capable microphone: streams the angle it is told."""

    def __init__(self, azimuth: float = 0.0) -> None:
        self.azimuth = azimuth

    @property
    def available(self) -> bool:
        return True

    @property
    def status_text(self) -> str:
        return "scripted test source"

    def read(self) -> list[DirectionReading]:
        return [DirectionReading(time.monotonic(), self.azimuth, 0.9)]


@pytest.fixture(scope="module")
def qt_app():
    return QApplication.instance() or QApplication([])


def settings_for(tmp_path, **extra):
    return load_settings(environ={
        "DATA_DIR": str(tmp_path / "data"), "LOG_DIR": str(tmp_path / "logs"),
        "MEDIA_DIR": str(tmp_path / "media"), "STT_ENABLED": "true", "TTS_ENABLED": "false",
        "MIC_SILENCE_SECONDS": "0.2", "MIC_NO_SPEECH_TIMEOUT_S": "1.0", "MIC_MAX_SECONDS": "3",
        "VISION_AUTO_LISTEN": "false", "VOICE_FOLLOW_UP": "false", **extra,
    })


def pump_until(qt_app, condition, timeout_s: float = 4.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while not condition():
        if time.monotonic() > deadline:
            return False
        qt_app.processEvents()
        time.sleep(0.01)
    return True


def pump(qt_app, seconds: float) -> None:
    pump_until(qt_app, lambda: False, seconds)


def test_without_a_capable_microphone_nothing_changes_and_the_dashboard_says_why(qt_app, tmp_path):
    application = Application(settings_for(tmp_path), QtLogHandler(), microphone=FakeMicrophone(), stt=FakeStt())
    try:
        assert not application.speaker.available
        assert application.voice.speaker_check is None  # voice behaves exactly as before
        dashboard = application.window.admin_screen._dashboard
        assert dashboard._direction_card.value.text() == "ไม่มีอุปกรณ์รองรับ"
        assert "ไมค์ในโน้ตบุ๊กไม่ถือว่ารองรับ" in dashboard._direction_card.note.text()
        assert dashboard._match_card.value.text() == "ยังจับคู่ไม่ได้"
    finally:
        application.shutdown()
        application.window.deleteLater()


def test_only_the_visitor_at_the_robot_is_answered(qt_app, tmp_path):
    camera, microphone, stt, direction = MockVisionSource(), FakeMicrophone(), FakeStt(), ScriptedDirection()
    application = Application(
        settings_for(tmp_path), QtLogHandler(), microphone=microphone, stt=stt,
        vision_source=camera, direction_source=direction,
    )
    try:
        assert pump_until(qt_app, lambda: application.voice.status is VoiceStatus.READY)
        assert pump_until(qt_app, lambda: application.vision.status is SourceStatus.RUNNING)
        visitor, passer_by = person(-0.5, 1.0), person(1.2, 3.0)  # at the robot / talking further off
        camera.set_people(visitor, passer_by)
        # The visitor's arrival makes the robot greet first; let that happen and stop it.
        assert pump_until(qt_app, lambda: application.vision.present)
        pump_until(qt_app, lambda: application.conversation.state.state is ConversationState.SPEAKING, 2.0)
        application.conversation.stop()
        screen = application.window.user_screen
        screen._clear_transcript()

        def speak_from(target):
            direction.azimuth = person_azimuth(target, GEOMETRY)
            transcribed = stt.transcribed
            application.conversation.stop()
            screen._on_talk_button()
            microphone.level = LOUD
            pump(qt_app, 0.6)
            microphone.level = QUIET
            assert pump_until(qt_app, lambda: stt.transcribed == transcribed + 1)
            pump(qt_app, 0.3)

        speak_from(passer_by)
        assert application.conversation.state.state is ConversationState.IDLE
        assert screen.transcript_texts() == []  # heard, but neither shown nor answered

        speak_from(visitor)
        assert pump_until(qt_app, lambda: application.conversation.state.state is ConversationState.SPEAKING)
        assert screen.transcript_texts()[0] == f"คุณ: {stt.text}"
        card = application.window.admin_screen._dashboard._match_card
        application.window.admin_screen._dashboard._show_speaker()
        assert "X -0.50" in card.note.text() and "Z 1.00" in card.note.text()
    finally:
        application.shutdown()
        application.window.deleteLater()
