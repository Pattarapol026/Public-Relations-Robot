"""The Admin's direction commands to the real hardware: settings, the link's rules, the page.

Everything runs against the local test broker on 127.0.0.1 and a fake hardware
client; nothing is sent to a real robot.
"""
from __future__ import annotations

import threading
import time

import paho.mqtt.client as mqtt
import pytest
from PySide6.QtWidgets import QApplication

from app.hardware_controller import GROUP_MOVE, GROUP_TURN, HardwareController
from config.settings import ConfigError, load_settings
from services.hardware_link import HardwareLink, HardwareLinkConfig, HardwareLinkError
from services.mqtt_service import wait_for
from tools.mqtt_broker import LocalBroker, free_port
from ui.admin_hardware import HardwareTab
from ui.fonts import apply_app_font

TOPIC = "robot/direction"
REPLY = "robot/r1/status"
STATUS = "robot/r1/state"


class FakeHardware:
    """Subscribes to the direction topic, keeps what it got, and answers if asked to."""

    def __init__(self, port: int, answer: bool) -> None:
        self.received: list[str] = []
        self.ready = threading.Event()
        self._answer = answer
        self._client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="fake-hardware")
        self._client.on_connect = lambda c, *_: c.subscribe(TOPIC, qos=1)
        self._client.on_subscribe = lambda *_: self.ready.set()
        self._client.on_message = self._on_message
        self._client.connect("127.0.0.1", port)
        self._client.loop_start()

    def _on_message(self, client, _userdata, message) -> None:
        text = message.payload.decode("utf-8")
        self.received.append(text)
        if self._answer:
            client.publish(REPLY, f"ok {text}", qos=1)

    def say_status(self, text: str, retain: bool = False) -> None:
        self._client.publish(STATUS, text, qos=1, retain=retain).wait_for_publish(3.0)

    def stop(self) -> None:
        self._client.loop_stop()
        self._client.disconnect()


@pytest.fixture
def broker():
    broker = LocalBroker(free_port())
    broker.start()
    yield broker
    broker.stop()


def make_link(port: int, clock=time.monotonic, **overrides) -> HardwareLink:
    values = {"host": "127.0.0.1", "port": port, "stop_payload": "stop", "reply_topic": REPLY,
              "status_topic": STATUS}
    values.update(overrides)
    link = HardwareLink(HardwareLinkConfig(**values), clock=clock)
    link.start()
    assert wait_for(lambda: link.is_connected, 5.0), link.status
    return link


@pytest.fixture
def hardware(broker):
    fake = FakeHardware(broker.port, answer=True)
    assert fake.ready.wait(5.0)
    yield fake
    fake.stop()


# --- settings ---


def test_the_hardware_link_is_off_unless_a_host_is_set():
    settings = load_settings(environ={})
    assert settings.hw_mqtt_host == "" and settings.hw_stop_payload == ""
    assert settings.hw_direction_topic == "robot/direction"
    assert (settings.hw_turn_max_deg, settings.hw_move_max_cm) == (180, 100)
    assert (settings.hw_turn_topic, settings.hw_move_topic) == ("robot/direction", "robot/direction")
    assert settings.hw_require_camera is False
    assert settings.hw_status_topic == "robot/r1/status"
    assert not hasattr(settings, "hw_speak_topic")  # the overview's SPEAK panel is gone
    assert settings.hw_topics == ("robot/direction", "robot/status", "robot/mode")


def test_hardware_settings_are_read_and_the_password_is_not_printed():
    settings = load_settings(environ={
        "MQTT_HW_HOST": "192.168.10.1", "MQTT_HW_STOP_PAYLOAD": "stop", "MQTT_HW_REPLY_TOPIC": "robot/r1/#",
        "MQTT_HW_PAYLOAD_FORMAT": "{direction}:{value}", "MQTT_HW_TURN_MAX_DEG": "90",
        "MQTT_HW_MOVE_MAX_CM": "30", "MQTT_HW_TURN_TOPIC": "robot/angle", "MQTT_HW_MOVE_TOPIC": "robot/move",
        "MQTT_HW_PASSWORD": "hw-secret-77",
    })
    assert settings.hw_mqtt_host == "192.168.10.1" and settings.hw_reply_topic == "robot/r1/#"
    assert (settings.hw_turn_max_deg, settings.hw_move_max_cm) == (90, 30)
    assert (settings.hw_turn_topic, settings.hw_move_topic) == ("robot/angle", "robot/move")
    assert {"robot/angle", "robot/move"} <= set(settings.hw_topics)
    assert "hw-secret-77" not in repr(settings)


@pytest.mark.parametrize("environ", [
    {"MQTT_HW_HOST": "192.168.10.1; rm"},
    {"MQTT_HW_REPLY_TOPIC": "robot/direction"},  # our own message would count as the answer
    {"MQTT_HW_PAYLOAD_FORMAT": "{direction}"},
    {"MQTT_HW_PAYLOAD_FORMAT": "{direction} {value} {speed}"},
    {"MQTT_HW_TURN_MAX_DEG": "0"},
    {"MQTT_HW_TURN_MAX_DEG": "720"},
    {"MQTT_HW_MOVE_MAX_CM": "fast"},
    {"MQTT_HW_MOVE_TOPIC": "robot/r1/status"},  # where the robot reports
    {"MQTT_HW_STOP_PAYLOAD": "stop\nleft 100"},
    {"MQTT_HW_STATUS_TOPIC": "robot/direction"},  # our own command would count as the robot's status
    {"MQTT_HW_TOPICS": "robot/direction,robot/#"},
    {"MQTT_HW_TOPICS": "robot/r1/status"},  # where the robot reports: not a place to send commands
])
def test_invalid_hardware_settings_are_refused(environ):
    with pytest.raises(ConfigError):
        load_settings(environ=environ)


# --- the link ---


def test_a_direction_reaches_the_hardware_as_plain_text_and_its_answer_is_kept(broker, hardware):
    link = make_link(broker.port)
    try:
        record = link.send_direction("left", 50)
        assert record.payload == "left 50" and record.topic == TOPIC
        assert wait_for(lambda: hardware.received == ["left 50"], 3.0)
        assert wait_for(lambda: link.last_command.acked and link.last_command.reply == "ok left 50", 3.0)
    finally:
        link.stop()


def test_without_an_answer_nothing_claims_the_hardware_got_it(broker):
    fake = FakeHardware(broker.port, answer=False)
    assert fake.ready.wait(5.0)
    link = make_link(broker.port)
    try:
        link.send_direction("right", 20)
        assert wait_for(lambda: link.last_command.acked, 3.0)  # the broker has it...
        time.sleep(0.3)
        assert link.last_command.reply == ""  # ...but no word from the hardware
    finally:
        link.stop()
        fake.stop()


@pytest.mark.parametrize("direction", ["forward", "backward", "left", "right"])
def test_the_four_directions_are_sent_as_plain_text(broker, hardware, direction):
    link = make_link(broker.port)
    try:
        assert link.send_direction(direction, 40).payload == f"{direction} 40"
        assert wait_for(lambda: hardware.received == [f"{direction} 40"], 3.0)
    finally:
        link.stop()


@pytest.mark.parametrize("direction, value", [("back", 50), ("up", 50), ("Forward", 50), ("left", 181),
                                              ("left", 0), ("forward", 101), ("backward", 0),
                                              ("left", 5.5), ("forward", True)])
def test_only_the_four_directions_within_the_range_are_sent(broker, direction, value):
    link = make_link(broker.port)
    try:
        assert link.direction_refusal(direction, value)
        with pytest.raises(HardwareLinkError):
            link.send_direction(direction, value)
        assert link.last_command is None
    finally:
        link.stop()


def test_no_direction_without_a_stop_message(broker):
    link = make_link(broker.port, stop_payload="")
    try:
        assert "MQTT_HW_STOP_PAYLOAD" in link.direction_refusal("left", 50)
        assert link.stop_refusal()
    finally:
        link.stop()


def test_one_direction_a_second_but_stop_is_never_held_back(broker, hardware):
    now = [100.0]
    link = make_link(broker.port, clock=lambda: now[0])
    try:
        link.send_direction("left", 50)
        assert "รออีก" in link.direction_refusal("right", 50)
        assert link.send_stop().payload == "stop"  # straight after: not held back
        now[0] += 1.01
        assert link.direction_refusal("right", 50) == ""
        assert wait_for(lambda: hardware.received == ["left 50", "stop"], 3.0)
    finally:
        link.stop()


def test_a_stored_status_is_not_taken_as_the_robot_being_online_now(broker, hardware):
    hardware.say_status("hello", retain=True)  # what the robot said some time ago
    link = make_link(broker.port)
    try:
        assert wait_for(lambda: link.robot_status is not None, 3.0)
        assert link.robot_status.text == "hello" and link.robot_status.retained
        hardware.say_status("online")
        assert wait_for(lambda: link.robot_status.text == "online", 3.0)
        assert not link.robot_status.retained
    finally:
        link.stop()
        hardware.say_status("", retain=True)  # clear the stored copy


def test_a_direction_can_go_to_another_topic_but_never_to_one_the_robot_talks_on(broker):
    link = make_link(broker.port)
    try:
        assert link.send_direction("left", 10, "robot/mode").topic == "robot/mode"
        for topic in (STATUS, REPLY, "robot/#", "robot/+/x", ""):
            assert link.topic_refusal(topic)
    finally:
        link.stop()


class MemoryStore:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def get(self, key, default=None):
        return self.values.get(key, default)

    def set(self, key, value) -> None:
        self.values[key] = value


def test_one_topic_serves_turns_moves_and_typed_messages(qt_app, broker):
    store = MemoryStore()
    presets = ("robot/direction", "robot/status", "robot/mode")
    listener = Listener(broker.port, "robot/#")
    controller = HardwareController(make_link(broker.port), topics=presets, store=store)
    try:
        assert controller.topics == presets and controller.topic == TOPIC
        assert controller.select_topic("robot/r1/cmd") == ""
        assert controller.topics[-1] == "robot/r1/cmd" and controller.topic == "robot/r1/cmd"
        assert controller.select_topic(STATUS)  # where the robot speaks: refused
        assert controller.remove_topic("robot/mode")  # from .env: stays
        controller.confirm_area(True)
        assert controller.send_direction("right", 45) == ""
        controller.link._last_direction_at -= 2  # past the one-second pause
        assert controller.send_direction("forward", 25) == ""
        controller.link._last_direction_at -= 2
        assert controller.send_text("ไปห้องกิจกรรมหน่อย") == ""
        assert wait_for(lambda: [t for t, _ in listener.received if t != STATUS] == ["robot/r1/cmd"] * 3, 3.0)
        # STOP: to the topic in use, and to the .env direction topic too, so it always gets there.
        assert controller.send_stop() == ""
        assert wait_for(lambda: {("robot/r1/cmd", "stop"), (TOPIC, "stop")} <= set(listener.received), 3.0)
    finally:
        controller.shutdown()
        listener.stop()

    again = HardwareController(make_link(broker.port), topics=presets, store=store)  # after a restart
    try:
        assert again.topic == "robot/r1/cmd"
        assert again.remove_topic("robot/r1/cmd") == ""
        assert "robot/r1/cmd" not in again.topics and again.topic == TOPIC
    finally:
        again.shutdown()


@pytest.mark.parametrize("stored", [
    {"hardware.topic": '"robot/cmd"'},  # one choice, from before turns and moves were apart
    {"hardware.topic.turn": '"robot/cmd"', "hardware.topic.move": '"robot/direction"'},  # one each
])
def test_an_earlier_choice_carries_over(qt_app, broker, stored):
    store = MemoryStore()
    store.values.update({"hardware.topics": '["robot/cmd"]', **stored})
    controller = HardwareController(make_link(broker.port), store=store)
    try:
        assert controller.topic == "robot/cmd"
    finally:
        controller.shutdown()


def test_the_page_has_one_topic_bar_and_a_typed_topic_is_used_when_sending(qt_app, broker):
    received: list[tuple[str, str]] = []
    listener = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="topic-listener")
    ready = threading.Event()
    listener.on_connect = lambda c, *_: c.subscribe("robot/#", qos=1)
    listener.on_subscribe = lambda *_: ready.set()
    listener.on_message = lambda _c, _u, m: received.append((m.topic, m.payload.decode()))
    listener.connect("127.0.0.1", broker.port)
    listener.loop_start()
    assert ready.wait(5.0)
    controller = HardwareController(make_link(broker.port), topics=(TOPIC, "robot/status", "robot/mode"))
    tab = HardwareTab(controller)
    card = tab.turn_card
    try:
        assert [tab.topic_box.itemText(i) for i in range(tab.topic_box.count())] == [TOPIC, "robot/status", "robot/mode"]
        assert not hasattr(card, "topic_box") and not hasattr(tab.message_card, "topic_box")  # one bar only
        assert not tab.remove_topic_button.isEnabled()  # from .env
        tab.confirm_box.setChecked(True)
        tab.topic_box.setCurrentText("robot/newtopic")  # typed, not applied yet
        card.value_spin.setValue(7)
        card.buttons["right"].click()
        tab.stop_button.click()
        assert pump_until(qt_app, lambda: ("robot/direction", "stop") in received)
        assert ("robot/newtopic", "right 7") in received and ("robot/newtopic", "stop") in received
        assert controller.topic == "robot/newtopic" and "robot/newtopic" in controller.topics
        assert tab.remove_topic_button.isEnabled()  # typed by the Admin: may be removed
        assert "→ robot/newtopic" in tab.move_card.sample_label.text() or "ยังกดไม่ได้" in tab.move_card.sample_label.text()
        assert "robot/newtopic" in tab.topic_info.text()
    finally:
        controller.shutdown()
        listener.loop_stop()
        listener.disconnect()


def test_nothing_is_sent_when_not_connected():
    link = HardwareLink(HardwareLinkConfig(host="127.0.0.1", port=free_port(), stop_payload="stop"))
    assert "เชื่อมต่อ" in link.direction_refusal("left", 50)
    with pytest.raises(HardwareLinkError):
        link.send_stop()


# --- the Admin page ---


@pytest.fixture(scope="module")
def qt_app():
    qt_app = QApplication.instance() or QApplication([])
    apply_app_font(qt_app)
    return qt_app


def pump_until(qt_app, condition, timeout_s: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while not condition():
        if time.monotonic() > deadline:
            return False
        qt_app.processEvents()
        time.sleep(0.01)
    return True


def test_the_page_says_how_to_set_up_when_there_is_no_link(qt_app):
    tab = HardwareTab(None)
    assert "MQTT_HW_HOST" in tab.link_info.text()
    assert not tab.stop_button.isEnabled()
    assert not any(button.isEnabled() for button in tab.direction_buttons.values())


def test_directions_need_the_area_confirmed_again_after_the_admin_leaves(qt_app, broker, hardware):
    log = []
    controller = HardwareController(make_link(broker.port), audit=lambda *entry: log.append(entry))
    tab = HardwareTab(controller)
    try:
        pump_until(qt_app, lambda: tab.stop_button.isEnabled())
        assert tab.stop_button.isEnabled()  # STOP needs no confirmation
        assert not tab.direction_buttons["left"].isEnabled()

        tab.confirm_box.setChecked(True)
        assert set(tab.turn_card.buttons) == {"left", "right"} and set(tab.move_card.buttons) == {"forward", "backward"}
        assert tab.turn_card.value_spin.suffix() == " องศา" and tab.move_card.value_spin.suffix() == " ซม."
        assert all(button.isEnabled() for button in tab.direction_buttons.values())
        next(b for b in tab.turn_card.preset_buttons if b.text() == "30").click()  # a quick pick
        assert tab.turn_card.value_spin.value() == 30
        tab.direction_buttons["left"].click()
        assert pump_until(qt_app, lambda: "ฮาร์ดแวร์ยืนยัน" in tab.hardware_label.text())
        assert hardware.received == ["left 30"]
        assert "Broker: รับแล้ว" in tab.broker_label.text()
        assert ("admin", "hardware.direction", "topic=robot/direction payload=left 30 result=sent") in log

        tab.reset()  # leaving the Admin
        assert not controller.area_confirmed and not tab.confirm_box.isChecked()
        assert not tab.direction_buttons["right"].isEnabled()
    finally:
        controller.shutdown()


def test_the_camera_rule_can_hold_directions_back(qt_app, broker):
    controller = HardwareController(make_link(broker.port), safety_check=lambda: "person_too_close")
    try:
        controller.confirm_area(True)
        assert "ใกล้" in controller.send_direction("left", 50)
        assert controller.last_command is None
        assert controller.send_stop() == ""  # stopping is never held back
    finally:
        controller.shutdown()


def test_with_the_real_robot_set_up_the_overview_has_no_mqtt_panel(qt_app, tmp_path, broker, hardware):
    from app.application import Application
    from ui.log_bridge import QtLogHandler

    hardware.say_status("hello", retain=True)
    settings = load_settings(environ={
        "DATA_DIR": str(tmp_path / "data"), "LOG_DIR": str(tmp_path / "logs"),
        "MEDIA_DIR": str(tmp_path / "media"), "STT_ENABLED": "false", "TTS_ENABLED": "false",
        "VISION_ENABLED": "false",
        "MQTT_HW_HOST": "127.0.0.1", "MQTT_HW_PORT": str(broker.port), "MQTT_HW_STOP_PAYLOAD": "stop",
        "MQTT_HW_STATUS_TOPIC": STATUS,
    })
    application = Application(settings, QtLogHandler())
    try:
        dashboard = application.window.admin_screen._dashboard
        assert not hasattr(dashboard, "hardware_panel")
        assert not dashboard.mqtt_panel.isVisibleTo(dashboard)  # the simulator panel stays off too
        tab = application.window.admin_screen._hardware_tab  # the robot's status moved here
        assert pump_until(qt_app, lambda: "ข้อความเก่าที่ broker เก็บไว้" in tab.robot_tag.text())
        assert hardware.received == []  # connecting sends nothing
    finally:
        application.shutdown()
        application.window.deleteLater()
        hardware.say_status("", retain=True)


# --- typed messages and listening ---


class Listener:
    """Another client on the broker: hears what the app sends, and can talk to it."""

    def __init__(self, port: int, topic: str) -> None:
        self.received: list[tuple[str, str]] = []
        ready = threading.Event()
        self._client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="typed-listener")
        self._client.on_connect = lambda c, *_: c.subscribe(topic, qos=1)
        self._client.on_subscribe = lambda *_: ready.set()
        self._client.on_message = lambda _c, _u, m: self.received.append((m.topic, m.payload.decode("utf-8")))
        self._client.connect("127.0.0.1", port)
        self._client.loop_start()
        assert ready.wait(5.0)

    def say(self, topic: str, text: str) -> None:
        self._client.publish(topic, text.encode("utf-8"), qos=1).wait_for_publish(3.0)

    def stop(self) -> None:
        self._client.loop_stop()
        self._client.disconnect()


@pytest.mark.parametrize("topic, refused", [
    ("robot/#", False), ("robot/+/reply", False), ("#", False), ("robot/r1", False),
    ("robot/#/x", True), ("robot/a+b", True), ("", True), ("โรบอท/x", True),
])
def test_which_topics_can_be_listened_to(topic, refused):
    from services.hardware_link import listen_refusal

    assert bool(listen_refusal(topic)) is refused


def test_a_typed_message_is_sent_as_typed_under_the_same_rules_as_a_direction(qt_app, broker):
    listener = Listener(broker.port, "robot/#")
    log = []
    controller = HardwareController(make_link(broker.port), audit=lambda *entry: log.append(entry))
    try:
        assert controller.topic == TOPIC  # the robot's one topic
        assert "ยืนยัน" in controller.send_text("ไปห้องกิจกรรมหน่อย")  # the area is not confirmed yet
        controller.confirm_area(True)
        assert controller.text_refusal("") and controller.text_refusal("a\nb")
        assert controller.text_refusal("x" * 201)
        assert controller.send_text("ไปห้องกิจกรรมหน่อย") == ""
        assert wait_for(lambda: (TOPIC, "ไปห้องกิจกรรมหน่อย") in listener.received, 3.0)
        assert "รอ" in controller.send_text("goto activity_room")  # one command a second
        controller.link._last_direction_at -= 2
        assert controller.select_topic("robot/goto") == ""
        assert controller.send_text("goto activity_room") == ""
        assert wait_for(lambda: ("robot/goto", "goto activity_room") in listener.received, 3.0)
        assert ("admin", "hardware.text", f"topic={TOPIC} payload=ไปห้องกิจกรรมหน่อย result=sent") in log
    finally:
        controller.shutdown()
        listener.stop()


def test_messages_on_listened_topics_arrive_and_the_choice_is_kept(qt_app, broker):
    store = MemoryStore()
    talker = Listener(broker.port, "nothing/here")
    controller = HardwareController(make_link(broker.port), store=store)
    got = []
    controller.message_received.connect(got.append)
    try:
        assert controller.listen("robot/r1/#") == ""
        assert controller.listen("robot/#/x")  # refused
        assert controller.listening == ("robot/r1/#",)
        time.sleep(0.3)  # the subscription reaches the broker
        talker.say("robot/r1/arrived", "ถึงห้องกิจกรรมแล้ว")
        talker.say(STATUS, "ready")  # the status topic is always heard
        assert pump_until(qt_app, lambda: {(m.topic, m.text) for m in got} >= {
            ("robot/r1/arrived", "ถึงห้องกิจกรรมแล้ว"), (STATUS, "ready")})
    finally:
        controller.shutdown()

    again = HardwareController(make_link(broker.port), store=store)  # after a restart
    try:
        assert again.listening == ("robot/r1/#",)
        again.unlisten("robot/r1/#")
        assert again.listening == ()
    finally:
        again.shutdown()
        talker.stop()


def test_the_page_sends_a_typed_message_and_shows_what_comes_back(qt_app, broker):
    talker = Listener(broker.port, TOPIC)
    controller = HardwareController(make_link(broker.port))
    tab = HardwareTab(controller)
    card = tab.message_card
    try:
        card.text_input.setText("ไปห้องกิจกรรมหน่อย")
        assert not card.send_button.isEnabled() and "ยังส่งไม่ได้" in card.sample_label.text()
        tab.confirm_box.setChecked(True)
        assert card.send_button.isEnabled() and f"→ {TOPIC}" in card.sample_label.text()
        card.send_button.click()
        assert pump_until(qt_app, lambda: (TOPIC, "ไปห้องกิจกรรมหน่อย") in talker.received)
        assert card.text_input.text() == ""  # ready for the next one
        assert "ส่งข้อความ \"ไปห้องกิจกรรมหน่อย\"" in tab.sent_label.text()

        card.listen_input.setText("robot/r1/reply")
        card.listen_button.click()
        assert card.listening_box.currentText() == "robot/r1/reply"
        time.sleep(0.3)
        talker.say("robot/r1/reply", "กำลังไปห้องกิจกรรม")
        assert pump_until(qt_app, lambda: "กำลังไปห้องกิจกรรม" in card.received_box.toPlainText())
        assert "robot/r1/reply" in card.received_box.toPlainText()
        card.unlisten_button.click()
        assert controller.listening == ()
    finally:
        controller.shutdown()
        talker.stop()


# --- the visitor screen's buttons ---


def visitor_app(tmp_path, broker, **extra):
    from app.application import Application
    from ui.log_bridge import QtLogHandler

    settings = load_settings(environ={
        "DATA_DIR": str(tmp_path / "data"), "LOG_DIR": str(tmp_path / "logs"),
        "MEDIA_DIR": str(tmp_path / "media"), "STT_ENABLED": "false", "TTS_ENABLED": "false",
        "VISION_ENABLED": "false",
        "MQTT_HW_HOST": "127.0.0.1", "MQTT_HW_PORT": str(broker.port), "MQTT_HW_STOP_PAYLOAD": "stop",
        "MQTT_HW_STATUS_TOPIC": STATUS, **extra,
    })
    return Application(settings, QtLogHandler())


def ask(application, text):
    """What the robot says to a spoken (or typed) command."""
    spoken = []
    application.conversation.reply_ready.connect(spoken.append)
    application.conversation.ask(text, "voice")
    return spoken


def test_the_visitor_screen_has_no_turn_or_move_buttons_but_says_whose_robot_it_is(qt_app, tmp_path, broker):
    application = visitor_app(tmp_path, broker)
    screen = application.window.user_screen
    try:
        assert not hasattr(screen, "_drive_buttons") and not hasattr(screen, "_amount_sliders")
        texts = [label.text() for label in screen.findChildren(__import__("PySide6.QtWidgets", fromlist=["QLabel"]).QLabel)]
        assert "หุ่นยนต์ Aira จากวิทยาลัยเทคนิคสัตหีบ" in texts
    finally:
        application.shutdown()
        application.window.deleteLater()


def test_a_spoken_move_goes_to_the_real_robot_only_once_the_admin_allows_it(qt_app, tmp_path, broker, hardware):
    application = visitor_app(tmp_path, broker)
    controller = application.hardware
    try:
        assert pump_until(qt_app, lambda: application.hardware.link.is_connected)
        spoken = ask(application, "หันซ้าย 70 องศา")
        assert pump_until(qt_app, lambda: spoken)
        assert "ผู้ดูแลยังไม่เปิด" in spoken[0] and hardware.received == []

        controller.set_visitor_enabled(True)  # the Admin page's switch
        spoken.clear()
        application.conversation.stop()
        application.conversation.ask("หันซ้าย 70 องศา", "voice")
        assert pump_until(qt_app, lambda: hardware.received == ["left 70"])
        assert pump_until(qt_app, lambda: spoken and "หันซ้าย 70 องศา" in spoken[0])
        assert pump_until(qt_app, lambda: controller.link.seconds_until_next_direction() == 0, 3.0)

        application.conversation.stop()
        application.conversation.ask("เดินหน้า 20 เซน", "voice")
        assert pump_until(qt_app, lambda: hardware.received[-1:] == ["forward 20"])
        assert pump_until(qt_app, lambda: controller.link.seconds_until_next_direction() == 0, 3.0)

        application.conversation.stop()
        application.conversation.ask("หันขวา", "voice")  # no angle said: 50 degrees
        assert pump_until(qt_app, lambda: hardware.received[-1:] == ["right 50"])
        assert pump_until(qt_app, lambda: controller.link.seconds_until_next_direction() == 0, 3.0)

        application.conversation.stop()
        application.conversation.ask("ถอยหลัง", "voice")  # no distance said: 30 cm
        assert pump_until(qt_app, lambda: hardware.received[-1:] == ["backward 30"])
        log = [(e.actor, e.action) for e in application.content.recent_activity()]
        assert ("touch", "hardware.direction") in log
    finally:
        application.shutdown()
        application.window.deleteLater()


def test_back_to_where_it_was_walks_the_moves_back_in_reverse_order(qt_app, tmp_path, broker, hardware):
    application = visitor_app(tmp_path, broker)
    controller = application.hardware
    try:
        assert pump_until(qt_app, lambda: controller.link.is_connected)
        controller.set_visitor_enabled(True)
        for text, wanted in (("หันซ้าย 20 องศา", "left 20"), ("เดินหน้า 10 เซน", "forward 10")):
            application.conversation.stop()
            application.conversation.ask(text, "voice")
            assert pump_until(qt_app, lambda: hardware.received[-1:] == [wanted])
            assert pump_until(qt_app, lambda: controller.link.seconds_until_next_direction() == 0, 3.0)
        application.conversation.stop()
        application.conversation.ask("กลับมาที่เดิม", "voice")
        assert pump_until(qt_app, lambda: hardware.received[-2:] == ["backward 10", "right 20"], 8.0)  # last move first
        assert controller.journal == ()
        spoken = ask(application, "กลับมาที่เดิม")
        assert pump_until(qt_app, lambda: spoken and "อยู่ที่เดิมแล้ว" in spoken[0])  # nothing left to undo
    finally:
        application.shutdown()
        application.window.deleteLater()


def test_a_spoken_move_says_why_not_while_the_robot_is_not_connected(qt_app, tmp_path):
    from app.application import Application
    from ui.log_bridge import QtLogHandler

    settings = load_settings(environ={
        "DATA_DIR": str(tmp_path / "data"), "LOG_DIR": str(tmp_path / "logs"),
        "MEDIA_DIR": str(tmp_path / "media"), "STT_ENABLED": "false", "TTS_ENABLED": "false",
        "VISION_ENABLED": "false", "MQTT_HW_HOST": "127.0.0.1", "MQTT_HW_PORT": str(free_port()),
        "MQTT_HW_STOP_PAYLOAD": "stop",
    })
    application = Application(settings, QtLogHandler())
    screen = application.window.user_screen
    try:
        application.hardware.set_visitor_enabled(True)
        assert screen._robot_chip.text() in ("หุ่นยนต์: ไม่พร้อม", "หุ่นยนต์: กำลังเชื่อมต่อ")
        spoken = ask(application, "เดินหน้า 20 เซน")
        assert pump_until(qt_app, lambda: spoken)
        assert "สั่งไม่ได้" in spoken[0] and "ยังไม่ได้เชื่อมต่อกับหุ่นยนต์" in spoken[0]
        screen._stop_robot()
        assert "ส่งคำสั่งหยุดไม่สำเร็จ" in screen._message_bar.text()
    finally:
        application.shutdown()
        application.window.deleteLater()


def test_the_admin_has_the_direction_page_and_the_link_stays_off_by_default(qt_app, tmp_path):
    from app.application import Application
    from ui.log_bridge import QtLogHandler

    settings = load_settings(environ={
        "DATA_DIR": str(tmp_path / "data"), "LOG_DIR": str(tmp_path / "logs"),
        "MEDIA_DIR": str(tmp_path / "media"), "STT_ENABLED": "false", "TTS_ENABLED": "false",
        "VISION_ENABLED": "false",
    })
    application = Application(settings, QtLogHandler())
    try:
        assert application.hardware is None
        admin = application.window.admin_screen
        titles = [title for _group, title, _hint in admin._page_info]
        assert "ควบคุมหุ่นยนต์" in titles and "หุ่นยนต์จำลอง" in titles  # no real robot: the simulator too
        assert "ยังไม่ได้ตั้งค่า" in admin._dashboard._hardware_card.value.text()
        assert admin._dashboard.mqtt_panel.isVisibleTo(admin._dashboard)  # the simulator panel, as it was
    finally:
        application.shutdown()
        application.window.deleteLater()


def test_with_the_real_robot_the_admin_shows_it_and_leaves_out_what_is_not_used(qt_app, tmp_path, broker):
    """The overview and the header follow the real robot; the simulator's page and cards are left out."""
    from app.application import Application
    from ui.log_bridge import QtLogHandler

    settings = load_settings(environ={
        "DATA_DIR": str(tmp_path / "data"), "LOG_DIR": str(tmp_path / "logs"),
        "MEDIA_DIR": str(tmp_path / "media"), "STT_ENABLED": "false", "TTS_ENABLED": "false",
        "VISION_ENABLED": "false", "MQTT_HW_HOST": "127.0.0.1", "MQTT_HW_PORT": str(broker.port),
    })
    application = Application(settings, QtLogHandler())
    try:
        admin = application.window.admin_screen
        titles = [title for _group, title, _hint in admin._page_info]
        assert "ควบคุมหุ่นยนต์" in titles
        assert not {"หุ่นยนต์จำลอง", "สื่อประชาสัมพันธ์", "ตั้งค่าหน้าจอ"} & set(titles)
        dashboard = admin._dashboard
        assert pump_until(qt_app, lambda: dashboard._robot_card.value.text() == "เชื่อมต่อแล้ว")
        assert admin._robot_tag.text() == "หุ่นยนต์: เชื่อมต่อแล้ว"
        for card in (dashboard._gateway_card, dashboard._safety_card, dashboard._mqtt_card,
                     dashboard._direction_card, dashboard._match_card, dashboard._hardware_card):
            assert not card.isVisibleTo(dashboard)
    finally:
        application.shutdown()
        application.window.deleteLater()
