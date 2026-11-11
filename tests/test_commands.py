"""Robot commands from speech and touch, and the rule that keeps the robot still near people."""
from __future__ import annotations

import dataclasses
import json
import time

import pytest
from PySide6.QtWidgets import QApplication, QPushButton

from app.application import Application
from app.state import ConversationState
from config.settings import PART_BASE, ConfigError, NamedTarget, load_settings
from robot.command_gateway import CommandGateway, CommandRequest, CommandStatus
from robot.interfaces import RobotState
from robot.mock_adapter import MockRobotAdapter
from robot.safety import ACTION, PERSON_TOO_CLOSE, SENSOR_UNAVAILABLE, Proximity, ProximityInterlock
from services.command_intent import INTENT_ACTION, INTENT_STOP, parse
from ui.fonts import apply_app_font
from ui.log_bridge import QtLogHandler
from vision.mock_source import MockVisionSource

ACTIONS = (
    NamedTarget("turn_left", "หันซ้าย", ("เลี้ยวซ้าย", "turn left"), PART_BASE),
    NamedTarget("turn_right", "หันขวา", ("เลี้ยวขวา", "turn right"), PART_BASE),
)

# The robot has no arms yet: none of these may move anything.
ARM_WORDS = ("โบกมือหน่อย", "ไหว้", "ทำท่าทักทาย", "ยกแขนขึ้น", "ยกมือ", "เอาแขนลง", "wave", "raise your arm")


# --- understanding what was said ---


@pytest.mark.parametrize(
    "text, kind, target",
    [
        ("หันซ้ายหน่อย", INTENT_ACTION, "turn_left"),
        ("ช่วยหันขวาให้ดูหน่อยได้ไหมครับ", INTENT_ACTION, "turn_right"),
        ("Turn right", INTENT_ACTION, "turn_right"),
        ("หัน ซ้าย", INTENT_ACTION, "turn_left"),
        ("หยุด", INTENT_STOP, None),
        ("หยุดก่อนครับ", INTENT_STOP, None),
        ("Stop!", INTENT_STOP, None),
    ],
)
def test_commands_are_recognised(text, kind, target):
    intent = parse(text, ACTIONS)
    assert intent is not None and intent.kind == kind
    assert (intent.target.id if intent.target else None) == target


@pytest.mark.parametrize(
    "text",
    [
        "ล็อบบี้อยู่ที่ไหน",
        "พาไปล็อบบี้หน่อยครับ",  # the robot does not walk: these are questions now
        "อยากไปโต๊ะประชาสัมพันธ์",
        "Take me to the lobby, please.",
        "เดินหน้า",
        "ถอยหลัง",
        "move forward",
        "go back",
        "วิทยาลัยหยุดวันไหนบ้าง",  # "หยุด" inside a question about holidays
        "หยุดเรียนวันเสาร์",
        "หุ่นยนต์โบกมือได้ไหม ทำอะไรได้อีกบ้าง",
        "ทำไมเด็กนักเรียนต้องยกมือก่อนถามคำถามครูในห้องเรียน",
        "สมัครเรียนได้ที่ไหน",
        "",
    ],
)
def test_questions_and_requests_to_walk_are_not_commands(text):
    assert parse(text, ACTIONS) is None


# --- settings ---


def write_config(tmp_path, robot: dict):
    path = tmp_path / "app_config.json"
    path.write_text(
        json.dumps({"institution_name": "x", "welcome_message": "x", "robot": robot}), encoding="utf-8"
    )
    return path


def test_shipped_configuration_names_the_moving_part_of_every_action(settings):
    parts = {action.id: action.part for action in settings.actions}
    assert parts == {"turn_left": PART_BASE, "turn_right": PART_BASE}
    assert settings.greet_action == ""  # greeted with words only
    assert settings.base_clearance_m == 0.6


@pytest.mark.parametrize("text", ARM_WORDS)
def test_shipped_configuration_has_no_arm_gestures(settings, text):
    assert parse(text, settings.actions) is None
    assert not hasattr(settings, "arm_clearance_m")


def test_shipped_configuration_has_nothing_that_walks(settings):
    ids = {action.id for action in settings.actions}
    assert not ids & {"step_forward", "step_back"}
    labels = " ".join(action.label + " " + " ".join(action.phrases) for action in settings.actions)
    assert "เดิน" not in labels and "ถอย" not in labels and "forward" not in labels
    assert not hasattr(settings, "locations")


@pytest.mark.parametrize(
    "robot",
    [
        {"actions": [{"id": "turn_left", "label": "x", "part": "leg"}]},
        {"actions": [{"id": "wave", "label": "x", "part": "arm"}]},  # no arms to move
        {"actions": [{"id": "turn_left", "label": "x", "phrases": "left"}]},
        {"actions": [{"id": "turn_left", "label": "x"}], "greet_action": "dance"},
    ],
)
def test_invalid_robot_configuration_is_rejected(tmp_path, robot):
    with pytest.raises(ConfigError):
        load_settings(write_config(tmp_path, robot), environ={})


# --- the interlock ---


class Sensor:
    def __init__(self, reliable: bool = True, nearest_m: float | None = None) -> None:
        self.reading = Proximity(reliable, nearest_m)

    def __call__(self) -> Proximity:
        if isinstance(self.reading, Exception):
            raise self.reading
        return self.reading


def make_interlock(sensor: Sensor) -> ProximityInterlock:
    return ProximityInterlock(sensor, 0.6, base_actions=["turn_left", "turn_right"])


def test_interlock_keeps_people_out_of_the_turning_robots_way():
    sensor = Sensor(nearest_m=0.7)
    interlock = make_interlock(sensor)
    assert interlock.check(ACTION, "turn_left") is None
    sensor.reading = Proximity(True, 0.5)
    assert interlock.check(ACTION, "turn_left") == PERSON_TOO_CLOSE
    sensor.reading = Proximity(True, None)  # nobody in view
    assert interlock.check(ACTION, "turn_right") is None


def test_interlock_refuses_when_it_cannot_see_and_ignores_actions_that_move_nothing():
    sensor = Sensor(reliable=False)
    interlock = make_interlock(sensor)
    assert interlock.check(ACTION, "turn_left") == SENSOR_UNAVAILABLE
    assert interlock.check(ACTION, "blink_lights") is None
    sensor.reading = RuntimeError("camera thread died")
    assert interlock.check(ACTION, "turn_right") == SENSOR_UNAVAILABLE


def test_gateway_does_not_pass_a_refused_command_to_the_robot():
    adapter = MockRobotAdapter(action_time_s=0.05)
    adapter.connect()
    sensor = Sensor(nearest_m=0.4)
    gateway = CommandGateway(
        adapter, ["turn_left", "turn_right"], timeout_s=2.0, interlock=make_interlock(sensor).check
    )
    result = gateway.submit(CommandRequest("action", "turn_left"))
    assert (result.status, result.code) == (CommandStatus.REJECTED, "person_too_close")
    assert adapter.get_status().state is RobotState.IDLE

    sensor.reading = Proximity(False)
    assert gateway.submit(CommandRequest("action", "turn_right")).code == "sensor_unavailable"

    sensor.reading = Proximity(True, 2.0)
    assert gateway.submit(CommandRequest("action", "turn_left")).ok
    assert gateway.submit(CommandRequest("action", "wave")).code == "not_allowed"  # no arms
    # The allowlist still comes first: the interlock cannot let anything new through.
    assert gateway.submit(CommandRequest("action", "dance")).code == "not_allowed"
    gateway.shutdown()


def test_gateway_refuses_if_the_interlock_itself_breaks():
    adapter = MockRobotAdapter(action_time_s=0.05)
    adapter.connect()

    def broken(_type, _target):
        raise RuntimeError("boom")

    gateway = CommandGateway(adapter, ["turn_left"], interlock=broken)
    assert gateway.submit(CommandRequest("action", "turn_left")).code == "sensor_unavailable"
    gateway.shutdown()


# --- in the application ---


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


def build(qt_app, tmp_path, greet_action: str = "", **environ):
    settings = load_settings(
        environ={
            "DATA_DIR": str(tmp_path / "data"), "LOG_DIR": str(tmp_path / "logs"),
            "MEDIA_DIR": str(tmp_path / "media"), "STT_ENABLED": "false", "TTS_ENABLED": "false",
            "VISION_DWELL_SECONDS": "0.1", "VISION_GREET_DWELL_SECONDS": "0.1", "VISION_GREET_STILL_SECONDS": "0",
            "VISION_LEAVE_SECONDS": "0.4", **environ,
        }
    )
    settings = dataclasses.replace(settings, greet_action=greet_action)
    camera = MockVisionSource()
    application = Application(settings, QtLogHandler(), vision_source=camera)
    pump(qt_app, 0.15)  # let the camera deliver its first result
    return application, camera


@pytest.fixture
def robot_app(qt_app, tmp_path):
    application, camera = build(qt_app, tmp_path)
    yield application, camera
    application.shutdown()
    application.window.deleteLater()


def robot_state(application) -> RobotState:
    return application.adapter.get_status().state


def robot_log(application) -> list[tuple[str, str, str]]:
    """(actor, action, detail) of robot entries in the activity log, oldest first."""
    entries = [e for e in application.content.recent_activity() if e.action.startswith("robot.")]
    return [(e.actor, e.action, e.detail) for e in reversed(entries)]


def test_a_typed_or_spoken_gesture_command_moves_the_robot_and_is_not_sent_to_the_ai(qt_app, robot_app):
    application, _camera = robot_app
    screen = application.window.user_screen
    application.conversation.ask("ช่วยหันซ้ายให้ดูหน่อย", "voice")
    assert screen.transcript_texts()[-2:] == ["คุณ: ช่วยหันซ้ายให้ดูหน่อย", "หุ่นยนต์: ได้ครับ หันซ้ายนะครับ"]
    assert application.conversation.state.state is ConversationState.SPEAKING
    assert not screen._feedback_row.isVisibleTo(screen)  # nothing to rate
    wait_until(qt_app, lambda: robot_state(application) is RobotState.EXECUTING)
    wait_until(qt_app, lambda: robot_log(application) != [])
    assert robot_log(application) == [("voice", "robot.action", "target=turn_left result=succeeded/ok")]
    assert application.analytics.summary(1).questions == 0  # a command is not a question


@pytest.mark.parametrize(
    "words", ["พาไปล็อบบี้หน่อยครับ", "ล็อบบี้อยู่ที่ไหน", "Take me to the lobby", "เดินหน้า", "โบกมือหน่อย", "ยกแขนขึ้น"]
)
def test_asking_the_robot_to_walk_or_use_arms_is_answered_as_a_question_and_nothing_moves(qt_app, robot_app, words):
    application, _camera = robot_app
    application.conversation.ask(words, "voice")
    wait_until(qt_app, lambda: application.analytics.summary(1).questions == 1)
    pump(qt_app, 0.2)
    assert robot_state(application) is RobotState.IDLE and robot_log(application) == []


def test_the_robot_screen_and_admin_have_no_button_that_walks_to_a_place_or_moves_an_arm(qt_app, robot_app):
    application, _camera = robot_app
    window = application.window
    labels = {button.text() for button in window.findChildren(QPushButton)}
    for gone in ("ไปที่ล็อบบี้", "ไปที่จุดต้อนรับ", "ไปที่โต๊ะประชาสัมพันธ์", "ใช่ พาไปเลย", "ไม่ต้อง",
                 "โบกมือ", "ไหว้ทักทาย", "ยกแขนขึ้น", "เอาแขนลง"):
        assert gone not in labels


def test_without_the_real_robot_the_screen_says_so_and_a_spoken_move_moves_nothing(qt_app, robot_app):
    """Moves go to the real robot over MQTT only (tests/test_hardware_link.py); the screen has no turn or move buttons."""
    application, camera = robot_app
    screen = application.window.user_screen
    camera.place_person(3.0)
    wait_until(qt_app, lambda: application.vision.nearest_m == pytest.approx(3.0))
    assert screen._robot_chip.text() == "หุ่นยนต์: ไม่พร้อม"  # never "(จำลอง)"
    assert not hasattr(screen, "_drive_buttons")
    pump(qt_app, 0.2)
    assert robot_state(application) is RobotState.IDLE and robot_log(application) == []


def test_the_ai_is_told_the_robot_cannot_walk(robot_app):
    application, _camera = robot_app
    service = application.conversation_service
    instruction = service.build_instruction("พาไปห้องสมุดหน่อย", [])
    assert "เดินไม่ได้" in instruction and "ยังไม่มีแขน" in instruction


def test_saying_stop_stops_a_moving_robot(qt_app, robot_app):
    application, _camera = robot_app
    application.robot.send("action", "turn_left", source="touch")
    wait_until(qt_app, lambda: robot_state(application) is RobotState.EXECUTING)
    application.conversation.ask("หยุด", "voice")
    assert application.window.user_screen.transcript_texts()[-1] == "หุ่นยนต์: หยุดแล้วครับ"
    wait_until(qt_app, lambda: robot_state(application) is RobotState.IDLE, timeout_s=1.0)
    assert ("voice", "robot.stop", "delivered") in robot_log(application)


def test_too_close_means_step_back_then_the_robot_does_it(qt_app, robot_app):
    application, camera = robot_app
    screen = application.window.user_screen
    camera.place_person(0.5)  # near enough to touch the screen, so in the way of a turn
    wait_until(qt_app, lambda: application.vision.nearest_m == pytest.approx(0.5))
    application.conversation.stop()  # the greeting is not what is being tested

    application.commands.request("turn_left", "touch")
    assert application.commands.waiting_for_space
    pump(qt_app, 0.5)
    assert robot_state(application) is RobotState.IDLE and robot_log(application) == []

    camera.place_person(1.4)
    wait_until(qt_app, lambda: robot_state(application) is RobotState.EXECUTING, timeout_s=2.0)
    assert not application.commands.waiting_for_space
    assert screen._message_bar.text() == "ขอบคุณครับ"


def test_waiting_for_space_gives_up_if_nobody_moves(qt_app, robot_app):
    application, camera = robot_app
    camera.place_person(0.5)
    # Let the greeting happen first; its message must not race the cancellation notice.
    wait_until(qt_app, lambda: application.vision.present)
    pump(qt_app, 0.2)
    reply = application.commands.request("turn_left", "touch")
    assert "ถอยออกไปหนึ่งก้าว" in reply
    application.commands._clearance_deadline.start(150)  # instead of waiting the full time
    wait_until(qt_app, lambda: not application.commands.waiting_for_space)
    assert "ยกเลิกคำสั่งแล้ว" in application.window.user_screen._message_bar.text()
    camera.place_person(2.0)
    pump(qt_app, 0.8)
    assert robot_log(application) == []  # the cancelled command does not come back to life


def test_someone_stepping_in_stops_a_movement_already_under_way(qt_app, robot_app):
    application, camera = robot_app
    camera.place_person(2.0)
    # Let the greeting happen first; its message must not race the safety notice.
    wait_until(qt_app, lambda: application.vision.present)
    pump(qt_app, 0.2)
    application.conversation.stop()
    application.robot.send("action", "turn_right", source="admin")
    wait_until(qt_app, lambda: robot_state(application) is RobotState.EXECUTING)

    camera.place_person(0.4)
    wait_until(qt_app, lambda: robot_state(application) is RobotState.IDLE, timeout_s=1.0)
    wait_until(qt_app, lambda: len(robot_log(application)) == 2)
    assert robot_log(application) == [
        ("safety", "robot.stop", "reason=person_too_close target=turn_right"),
        ("admin", "robot.action", "target=turn_right result=cancelled/cancelled"),
    ]
    assert "มีคนเข้ามาใกล้" in application.window.user_screen._message_bar.text()


def test_a_stalled_camera_means_no_movement(qt_app, robot_app):
    application, camera = robot_app
    camera.freeze()
    wait_until(qt_app, lambda: not application.vision.fresh, timeout_s=3.0)
    assert "กล้องยังไม่พร้อม" in application.commands.request("turn_right", "touch")
    application.robot.send("action", "turn_left", source="admin")  # straight to the gateway
    wait_until(qt_app, lambda: robot_log(application) != [])
    assert robot_log(application) == [("admin", "robot.action", "target=turn_left result=rejected/sensor_unavailable")]


def test_arriving_visitor_gets_no_gesture_while_the_robot_has_no_arms(qt_app, settings, tmp_path):
    application, camera = build(qt_app, tmp_path, greet_action=settings.greet_action)
    try:
        camera.place_person(2.5)
        wait_until(qt_app, lambda: application.window.user_screen.transcript_texts() != [])  # greeted in words
        pump(qt_app, 0.3)
        assert robot_state(application) is RobotState.IDLE and robot_log(application) == []
    finally:
        application.shutdown()
        application.window.deleteLater()


def test_a_configured_greeting_gesture_is_skipped_if_the_visitor_is_already_in_the_way(qt_app, tmp_path):
    # Two visitors in quick succession: no greeting cooldown, so both are greeted.
    application, camera = build(
        qt_app, tmp_path, greet_action="turn_left", VISION_GREET_COOLDOWN_SECONDS="0.01"
    )
    try:
        camera.place_person(2.5)
        wait_until(qt_app, lambda: robot_state(application) is RobotState.EXECUTING)
        wait_until(qt_app, lambda: robot_log(application) != [])
        assert robot_log(application) == [("auto", "robot.action", "target=turn_left result=succeeded/ok")]
        assert application.window.user_screen._message_bar.text().startswith("เข้ามาใกล้")  # no result message

        camera.clear()
        wait_until(qt_app, lambda: not application.vision.present)
        wait_until(qt_app, lambda: application.conversation.state.state is ConversationState.IDLE)  # the first greeting is over
        camera.place_person(0.5)  # walked straight up to the robot
        wait_until(qt_app, lambda: len(robot_log(application)) == 2)
        assert robot_log(application)[-1] == ("auto", "robot.action", "target=turn_left result=rejected/person_too_close")
        assert "ใกล้หุ่นยนต์เกินไป" not in application.window.user_screen._message_bar.text()
    finally:
        application.shutdown()
        application.window.deleteLater()


def test_voice_commands_can_be_switched_off_and_buttons_still_work(qt_app, tmp_path):
    application, _camera = build(qt_app, tmp_path, ROBOT_VOICE_COMMANDS="false")
    try:
        application.conversation.ask("หันซ้ายหน่อย")
        wait_until(qt_app, lambda: application.analytics.summary(1).questions == 1)  # treated as a question
        assert robot_log(application) == []
        application.conversation.stop()
        application.commands.request("turn_left", "touch")
        wait_until(qt_app, lambda: robot_state(application) is RobotState.EXECUTING)
    finally:
        application.shutdown()
        application.window.deleteLater()


def test_without_a_camera_there_is_no_interlock_and_admin_is_told(qt_app, settings):
    application = Application(settings, QtLogHandler())
    try:
        assert application.interlock is None
        assert "ปิดกล้องอยู่" in application.window.admin_screen._safety_label.text()
        application.commands.request("turn_left", "touch")
        wait_until(qt_app, lambda: robot_state(application) is RobotState.EXECUTING)
    finally:
        application.shutdown()
        application.window.deleteLater()
