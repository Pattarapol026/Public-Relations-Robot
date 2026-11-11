"""Stability fixes: visits that survive a detection gap, greeting cooldown, depth-less
detections, duplicate commands, honest results from the simulator, the camera
process watchdog and restart, and log categories."""
from __future__ import annotations

import logging
import time

import pytest
from PySide6.QtWidgets import QApplication

from app.application import Application
from config.settings import load_settings
from robot.command_gateway import CommandGateway, CommandRequest
from robot.mock_adapter import MockRobotAdapter
from robot.safety import ACTION, DISTANCE_UNKNOWN, PERSON_TOO_CLOSE, Proximity, ProximityInterlock
from ui.fonts import apply_app_font
from ui.log_bridge import (
    CATEGORY_COMMAND,
    CATEGORY_CONNECTION,
    CATEGORY_ERROR,
    CATEGORY_INFO,
    CATEGORY_SAFETY,
    CATEGORY_WARNING,
    QtLogHandler,
    classify,
)
from vision import oak_source, oak_worker
from vision.interfaces import PersonObservation, SourceStatus
from vision.mock_source import MockVisionSource
from vision.presence import PresenceConfig, PresenceEventType, PresenceTracker


def greeting_now(application) -> str:
    """What the robot says to an arriving visitor now: the greeting of the place in use."""
    from datetime import datetime

    return application.content.greeting_at(datetime.now())


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


def pump(qt_app, seconds: float) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        qt_app.processEvents()
        time.sleep(0.01)


def build(tmp_path, **environ) -> tuple[Application, MockVisionSource]:
    settings = load_settings(
        environ={
            "DATA_DIR": str(tmp_path / "data"), "LOG_DIR": str(tmp_path / "logs"),
            "MEDIA_DIR": str(tmp_path / "media"), "STT_ENABLED": "false", "TTS_ENABLED": "false",
            **environ,
        }
    )
    camera = MockVisionSource()
    return Application(settings, QtLogHandler(), vision_source=camera), camera


# --- presence: a short gap in detection is not the end of a visit ---


def test_visit_started_at_the_conversation_spot_survives_a_short_detection_gap(qt_app, tmp_path):
    # The settings found on the robot: greeting zone = conversation zone, and the
    # greeting needs a longer dwell, so a visit usually starts at the conversation spot.
    application, camera = build(
        tmp_path,
        VISION_ENGAGE_DISTANCE_M="1.5", VISION_GREET_DISTANCE_M="1.5", VISION_RELEASE_DISTANCE_M="2.0",
        VISION_DWELL_SECONDS="0.2", VISION_GREET_DWELL_SECONDS="2.0", VISION_GREET_STILL_SECONDS="0", VISION_LEAVE_SECONDS="3.0",
    )
    arrived, left = [], []
    application.vision.visitor_arrived.connect(arrived.append)
    application.vision.visitor_left.connect(left.append)
    try:
        camera.place_person(1.0)
        wait_until(qt_app, lambda: arrived)
        camera.clear()
        pump(qt_app, 2.0)  # longer than the conversation tracker's 1.5 s, shorter than 3 s
        assert left == []
        camera.place_person(1.0)
        pump(qt_app, 0.5)
        assert len(arrived) == 1  # the same visit, not a new visitor to greet again

        camera.clear()
        wait_until(qt_app, lambda: left, timeout_s=6.0)  # gone for good: the visit ends once
        assert len(left) == 1
    finally:
        application.shutdown()
        application.window.deleteLater()


def test_a_returning_visitor_is_not_greeted_again_within_the_cooldown(qt_app, tmp_path):
    application, camera = build(
        tmp_path, VISION_DWELL_SECONDS="0.1", VISION_GREET_DWELL_SECONDS="0.1", VISION_GREET_STILL_SECONDS="0",
        VISION_LEAVE_SECONDS="0.3", VISION_GREET_COOLDOWN_SECONDS="30",
    )
    screen = application.window.user_screen
    greeting = greeting_now(application)
    arrivals = []
    application.vision.visitor_arrived.connect(arrivals.append)
    try:
        camera.place_person(2.0)
        wait_until(qt_app, lambda: screen.transcript_texts() == [f"หุ่นยนต์: {greeting}"])
        application.conversation.stop()
        camera.clear()
        wait_until(qt_app, lambda: not application.vision.present)
        camera.place_person(2.0)
        wait_until(qt_app, lambda: len(arrivals) == 2)  # still counted as an arrival...
        pump(qt_app, 0.3)
        assert screen.transcript_texts() == [f"หุ่นยนต์: {greeting}"]  # ...but not greeted a second time (and nothing cleared)
    finally:
        application.shutdown()
        application.window.deleteLater()


def test_a_person_without_depth_keeps_a_visit_but_never_starts_one():
    config = PresenceConfig(engage_distance_m=1.5, release_distance_m=2.0, dwell_s=0.0, leave_s=1.0,
                            smoothing_samples=1)
    tracker = PresenceTracker(config)
    no_depth = PersonObservation(x_m=0.0, z_m=0.0, confidence=0.9)
    assert not no_depth.measured
    assert tracker.update([no_depth], 0.0) == [] and not tracker.present  # unknown distance: no visit
    assert tracker.unmeasured_count == 1

    [event] = tracker.update([PersonObservation(0.0, 1.0, 0.9)], 1.0)
    assert event.type is PresenceEventType.ARRIVED
    for second in (2.0, 3.0, 4.0):  # stepped too close for depth: still seen, the visit goes on
        assert tracker.update([no_depth], second) == []
    [event] = tracker.update([], 5.5)  # really gone
    assert event.type is PresenceEventType.LEFT


# --- safety: an unmeasured person is "distance unknown", not "0 m away" ---


def test_unmeasured_person_blocks_movement_with_its_own_reason():
    def interlock(proximity: Proximity) -> ProximityInterlock:
        return ProximityInterlock(lambda: proximity, 0.6, ["turn_left"])

    assert interlock(Proximity(True, None, unmeasured=True)).check(ACTION, "turn_left") == DISTANCE_UNKNOWN
    assert interlock(Proximity(True, 0.5, unmeasured=True)).check(ACTION, "turn_left") == PERSON_TOO_CLOSE
    assert interlock(Proximity(True, 2.0)).check(ACTION, "turn_left") is None


def test_nearest_distance_ignores_people_without_depth(qt_app, tmp_path):
    application, camera = build(tmp_path)
    try:
        camera.set_people(PersonObservation(0.0, 0.0, 0.9), PersonObservation(0.3, 2.4, 0.9))
        wait_until(qt_app, lambda: application.vision.unmeasured_nearby)
        assert application.vision.nearest_m == pytest.approx(2.42, abs=0.01)
        reply = application.commands.request("turn_left", "touch")
        assert "ถอย" in reply  # asked to step back and waited for, like "too close"
    finally:
        application.shutdown()
        application.window.deleteLater()


# --- commands ---


def test_a_double_tap_does_not_queue_the_same_command_twice(qt_app, tmp_path):
    application, camera = build(tmp_path)
    application.adapter._action_time_s = 0.3
    finished = []
    application.robot.command_finished.connect(finished.append)
    try:
        camera.place_person(3.0)
        pump(qt_app, 0.2)
        assert application.robot.send("action", "turn_left", source="admin")
        assert not application.robot.send("action", "turn_left", source="admin")  # still pending
        wait_until(qt_app, lambda: finished)
        pump(qt_app, 0.2)
        assert len(finished) == 1
        robot_rows = [e for e in application.content.recent_activity() if e.action == "robot.action"]
        assert len(robot_rows) == 1
    finally:
        application.shutdown()
        application.window.deleteLater()


def test_the_simulator_never_claims_that_the_robot_really_moved(qt_app, tmp_path):
    application, camera = build(tmp_path)
    application.adapter._action_time_s = 0.05
    finished = []
    application.robot.command_finished.connect(finished.append)
    try:
        assert application.robot.simulated
        camera.place_person(3.0)
        pump(qt_app, 0.2)
        application.robot.send("action", "turn_left", source="admin")
        wait_until(qt_app, lambda: finished)
        assert finished[0].ok and "จำลอง" in finished[0].message
        assert application.robot.last_command[1] is finished[0]
        assert "จำลอง" in application.window.admin_screen._robot_tag.text()
    finally:
        application.shutdown()
        application.window.deleteLater()


def test_a_safety_refusal_of_the_robots_own_gesture_is_not_a_warning(caplog):
    adapter = MockRobotAdapter(action_time_s=0.01)
    adapter.connect()
    gateway = CommandGateway(adapter, ["wave"], interlock=lambda _t, _a: PERSON_TOO_CLOSE)
    with caplog.at_level(logging.INFO, logger="robot.command_gateway"):
        gateway.submit(CommandRequest("action", "wave", source="auto"))
        gateway.submit(CommandRequest("action", "wave", source="touch"))
    levels = [r.levelname for r in caplog.records if "Command rejected" in r.getMessage()]
    assert levels == ["INFO", "WARNING"]  # the visitor's own request still shows as a warning
    gateway.shutdown()


# --- camera process ---


class _DeadParent:
    def is_alive(self) -> bool:
        return False


class _Event:
    def __init__(self) -> None:
        self.set_ = False

    def is_set(self) -> bool:
        return self.set_

    def wait(self, _seconds: float) -> None:
        pass


def test_camera_process_stops_when_the_app_is_gone():
    stop = oak_worker._StopWhenOrphaned(_Event())
    assert not stop.is_set()  # run from the main process: there is no parent to lose
    stop._parent = _DeadParent()  # the app was killed
    stop._next_check = 0.0
    assert stop.is_set()


class _FakeProcess:
    def __init__(self, alive: bool) -> None:
        self.alive = alive
        self.exitcode = None if alive else 1

    def is_alive(self) -> bool:
        return self.alive


class _FakeConnection:
    def poll(self) -> bool:
        return False

    def close(self) -> None:
        pass


def test_a_crashed_camera_process_is_restarted(monkeypatch):
    source = oak_source.OakVisionSource(oak_source.OakConfig(retry_seconds=0.0))
    starts = []

    def fake_start() -> None:
        starts.append(1)
        source._process = _FakeProcess(alive=True)
        source._connection = _FakeConnection()
        source._status = SourceStatus.STARTING

    monkeypatch.setattr(source, "start", fake_start)
    source.start()
    source._process.alive = False  # crashed
    source.latest()
    assert source.status is SourceStatus.UNAVAILABLE and "กำลังเริ่มใหม่" in source.detail
    source.latest()
    assert len(starts) == 2 and source.status is SourceStatus.STARTING

    for _ in range(oak_source.MAX_PROCESS_RESTARTS):  # it keeps crashing
        source._process.alive = False
        source.latest()
        source.latest()
    assert len(starts) == 1 + oak_source.MAX_PROCESS_RESTARTS
    source._process.alive = False
    source.latest()
    source.latest()
    assert len(starts) == 1 + oak_source.MAX_PROCESS_RESTARTS  # gives up and says so
    assert "ต้องเปิดโปรแกรมใหม่" in source.detail


# --- log categories ---


@pytest.mark.parametrize(
    ("level", "source", "message", "category"),
    [
        (logging.WARNING, "robot.command_gateway", "STOP requested (source=safety)", CATEGORY_SAFETY),
        (logging.WARNING, "robot.command_gateway",
         "Command rejected: id=1 type='action' target='wave' source=touch code=person_too_close",
         CATEGORY_SAFETY),
        (logging.WARNING, "vision.oak_source",
         "Camera unavailable: x (CameraNotFoundError: no free OAK camera on USB)", CATEGORY_CONNECTION),
        (logging.INFO, "robot.command_gateway",
         "Command accepted: id=1 type=action target=wave source=admin", CATEGORY_COMMAND),
        (logging.ERROR, "app", "Uncaught exception", CATEGORY_ERROR),
        (logging.WARNING, "app.speech_controller", "Speech model slow", CATEGORY_WARNING),
        (logging.INFO, "app.application", "Application started", CATEGORY_INFO),
    ],
)
def test_log_records_are_sorted_into_categories(level, source, message, category):
    assert classify(level, source, message) == category


def test_only_a_hand_held_up_to_the_camera_is_not_greeted_but_the_whole_person_is(qt_app, tmp_path):
    """A sign shown with just a hand gets its sign's reaction, not a hello; the greeting waits for the whole body."""
    application, camera = build(
        tmp_path, VISION_DWELL_SECONDS="0.1", VISION_GREET_DWELL_SECONDS="0.1", VISION_GREET_STILL_SECONDS="0",
    )
    screen = application.window.user_screen
    greeting = greeting_now(application)
    try:
        camera.place_person(1.2, box=(0.4, 0.3, 0.55, 0.5))  # a hand: a small box in the picture
        pump(qt_app, 1.5)
        assert application.vision.present and screen.transcript_texts() == []  # seen, not greeted
        camera.place_person(1.2, box=(0.3, 0.1, 0.7, 0.95))  # now the whole person
        wait_until(qt_app, lambda: screen.transcript_texts() == [f"หุ่นยนต์: {greeting}"], timeout_s=6.0)
    finally:
        application.shutdown()
        application.window.deleteLater()
