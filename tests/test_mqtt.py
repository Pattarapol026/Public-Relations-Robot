"""MQTT communication tests against a real broker on 127.0.0.1 and a simulated device.

No robot and no real hardware are involved: the "device" is tools/mqtt_device_simulator.py.
"""
from __future__ import annotations

import json
import threading
import time

import paho.mqtt.client as mqtt
import pytest

from config.settings import ConfigError, load_settings
from services.mqtt_service import (
    SCHEMA_VERSION,
    MessageError,
    MqttConfig,
    MqttError,
    MqttService,
    MqttStatus,
    make_message,
    parse_message,
    wait_for,
)
from tools.mqtt_broker import LocalBroker, free_port
from tools.mqtt_device_simulator import DeviceSimulator

PREFIX = "prrobot/test"


@pytest.fixture
def broker():
    broker = LocalBroker(free_port())
    broker.start()
    yield broker
    broker.stop()


def config(port: int, **overrides) -> MqttConfig:
    values = dict(port=port, client_id="prrobot-app-test", response_timeout_s=2.0,
                  reconnect_min_s=1.0, reconnect_max_s=2.0)
    values.update(overrides)
    return MqttConfig(**values)


@pytest.fixture
def service(broker):
    service = MqttService(config(broker.port))
    service.start()
    assert wait_for(lambda: service.is_connected, 5.0), service.status
    yield service
    service.stop()


@pytest.fixture
def device(broker):
    simulator = DeviceSimulator(port=broker.port, prefix=PREFIX)
    simulator.start()
    assert simulator.connected.wait(5.0)
    yield simulator
    simulator.stop()


class RawSubscriber:
    """A plain MQTT client that records everything on some topics, as bytes."""

    def __init__(self, port: int, *topics: str) -> None:
        self.messages: list[tuple[str, bytes]] = []
        self.ready = threading.Event()
        self._client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"raw-{time.monotonic_ns()}")

        def on_connect(client, *_args):
            client.subscribe([(topic, 1) for topic in topics])

        self._client.on_connect = on_connect
        self._client.on_subscribe = lambda *_args: self.ready.set()
        def on_message(_client, _userdata, message):
            # amqtt (the test broker) also hands a new subscriber retained messages of other
            # topics; a real broker does not, and clients ignore them, as here.
            if any(mqtt.topic_matches_sub(topic, message.topic) for topic in topics):
                self.messages.append((message.topic, message.payload))

        self._client.on_message = on_message
        self._client.connect("127.0.0.1", port)
        self._client.loop_start()
        assert self.ready.wait(5.0)

    def publish(self, topic: str, raw: bytes) -> None:
        self._client.publish(topic, raw, qos=1).wait_for_publish(2.0)

    def close(self) -> None:
        self._client.disconnect()
        self._client.loop_stop()


# --- message schema (no broker) ---


def test_message_round_trip_keeps_every_field():
    message = make_message("command", "app", "TEST_ECHO", {"text": "สวัสดี", "n": 1})
    parsed = parse_message(message.to_json())
    assert parsed == message
    data = json.loads(message.to_json())
    assert data["v"] == SCHEMA_VERSION and len(data["id"]) == 32 and data["ts"].endswith("+00:00")


@pytest.mark.parametrize(
    "raw",
    [
        b"not json",
        b"\xff\xfe",
        b"[1, 2]",
        json.dumps({"v": 2, "id": "a", "ts": "t", "type": "status", "source": "s"}).encode(),
        json.dumps({"v": 1, "ts": "t", "type": "status", "source": "s"}).encode(),
        json.dumps({"v": 1, "id": "a", "ts": "t", "type": "move", "source": "s"}).encode(),
        json.dumps({"v": 1, "id": "a", "ts": "t", "type": "response", "source": "s", "command": "PING"}).encode(),
        json.dumps({"v": 1, "id": "a", "ts": "t", "type": "status", "source": "s", "payload": [1]}).encode(),
        b"{" + b" " * 70_000 + b"}",
    ],
    ids=["text", "not-utf8", "array", "version-2", "no-id", "bad-type", "response-without-reply-to",
         "payload-not-object", "too-large"],
)
def test_malformed_payloads_are_rejected_with_a_reason(raw):
    with pytest.raises(MessageError):
        parse_message(raw)


def test_settings_read_mqtt_from_the_environment_and_hide_the_password(tmp_path):
    base = {"DATA_DIR": str(tmp_path / "d"), "LOG_DIR": str(tmp_path / "l"), "MEDIA_DIR": str(tmp_path / "m")}
    settings = load_settings(environ=base)
    assert not settings.mqtt_enabled  # off unless asked for: the app behaves as before
    settings = load_settings(environ={
        **base, "MQTT_ENABLED": "true", "MQTT_HOST": "broker.local", "MQTT_PORT": "8883",
        "MQTT_USERNAME": "robot", "MQTT_PASSWORD": "s3cret!", "MQTT_TLS": "true",
        "MQTT_TOPIC_PREFIX": "prrobot/test/",
    })
    assert (settings.mqtt_host, settings.mqtt_port, settings.mqtt_tls) == ("broker.local", 8883, True)
    assert settings.mqtt_topic_prefix == "prrobot/test"
    assert "s3cret!" not in repr(settings)
    with pytest.raises(ConfigError):
        load_settings(environ={**base, "MQTT_PORT": "99999"})


# --- with the broker ---


def test_connects_and_reports_each_status_with_a_time(broker):
    seen = []
    service = MqttService(config(broker.port))
    service.on_status.append(seen.append)
    service.start()
    try:
        assert wait_for(lambda: service.is_connected, 5.0)
        assert [s.status for s in seen] == [MqttStatus.CONNECTING, MqttStatus.CONNECTED]
        assert seen[-1].since.tzinfo is not None and seen[-1].detail == f"127.0.0.1:{broker.port}"
    finally:
        service.stop()
    assert service.status.status is MqttStatus.DISCONNECTED


def test_published_message_arrives_byte_for_byte(broker, service):
    raw = RawSubscriber(broker.port, f"{PREFIX}/command")
    try:
        message = make_message("command", "prrobot-app-test", "PING")
        service.publish("command", message)
        assert wait_for(lambda: raw.messages, 3.0)
        assert raw.messages == [(f"{PREFIX}/command", message.to_json())]
    finally:
        raw.close()


def test_safe_commands_are_answered_by_the_simulated_device(service, device):
    started = time.monotonic()
    pong = service.request("PING")
    assert pong.payload == {"ok": True, "pong": True, "simulated": True}
    assert pong.type == "response" and pong.source == device.client_id
    assert time.monotonic() - started < 1.0

    status = service.request("status")  # case does not matter
    assert status.payload["simulated"] is True and status.payload["actuators"] == "none"

    sent = {"text": "สวัสดีครับ", "numbers": [1, 2, 3], "nested": {"ok": True}}
    assert service.request("TEST_ECHO", sent).payload["echo"] == sent
    assert device.handled == ["PING", "STATUS", "TEST_ECHO"]


def test_speak_goes_busy_then_idle_and_stop_ends_it(service, device):
    states = []
    service.on_message.append(lambda m: states.append(m.payload) if m.type == "status" else None)
    reply = service.request("SPEAK", {"text": "สวัสดีครับ", "duration_s": 0.5})
    assert reply.payload["accepted"] is True
    assert wait_for(lambda: any(s.get("task_id") == reply.reply_to and s["state"] == "IDLE" for s in states), 3.0)
    assert [s["state"] for s in states if s.get("task_id") == reply.reply_to] == ["BUSY", "IDLE"]

    long = service.request("SPEAK", {"text": "ยาวมาก", "duration_s": 10})
    assert service.request("SPEAK", {"text": "อีก"}).payload == {"ok": False, "error": "busy", "task": "SPEAK", "simulated": True}
    assert service.request("STOP").payload["stopped_task"] == "SPEAK"
    assert wait_for(lambda: service.device_states[device.client_id]["state"] == "IDLE", 3.0)
    assert [s["result"] for s in states if s.get("task_id") == long.reply_to] == [None, "stopped"]
    assert service.request("STOP").payload["stopped_task"] is None  # nothing left to stop


def test_speak_without_text_is_refused_before_sending(service):
    for payload in (None, {}, {"text": "  "}, {"text": "x" * 501}):
        with pytest.raises(MqttError, match="SPEAK"):
            service.request("SPEAK", payload)


def test_each_client_keeps_its_own_status_and_apps_are_not_devices(broker, service, device):
    other_app = MqttService(config(broker.port, client_id="prrobot-second-app"))
    other_app.start()
    try:
        assert wait_for(lambda: other_app.is_connected, 5.0)
        # The device's retained status is not overwritten by an app connecting later.
        late = MqttService(config(broker.port, client_id="prrobot-late-app"))
        late.start()
        try:
            assert wait_for(lambda: device.client_id in late.device_states, 5.0)
            assert late.device_states[device.client_id]["state"] == "IDLE"
            assert set(late.device_states) == {device.client_id}  # the other apps are not devices
        finally:
            late.stop()
    finally:
        other_app.stop()


def test_anything_but_the_safe_commands_is_refused_before_sending(broker, service, device):
    raw = RawSubscriber(broker.port, f"{PREFIX}/command")
    try:
        for command in ("MOVE", "NAVIGATE", "ARM_UP", "STOP_MOTORS"):
            with pytest.raises(MqttError):
                service.request(command)
        time.sleep(0.3)
        assert raw.messages == []  # nothing reached the broker
        assert device.handled == [] and device.refused == []
    finally:
        raw.close()


def test_device_telemetry_reaches_the_software(service, device):
    received = []
    service.on_message.append(received.append)
    device.send_telemetry()
    assert wait_for(lambda: any(m.type == "telemetry" for m in received), 3.0)
    telemetry = next(m for m in received if m.type == "telemetry")
    assert telemetry.payload["simulated"] is True
    assert service.last_device_message.type in ("telemetry", "status")


def test_malformed_and_duplicate_messages_do_not_disturb_the_link(service, device):
    received = []
    service.on_message.append(received.append)
    device.publish("telemetry", b"{broken json")
    device.publish("response", b'{"v": 1, "id": "x"}')
    reading = make_message("telemetry", device.client_id, payload={"simulated": True, "n": 1}).to_json()
    device.publish("telemetry", reading)
    device.publish("telemetry", reading)  # the same message again, as QoS 1 may deliver it
    assert wait_for(lambda: service.counters["duplicates"] == 1, 3.0)
    assert service.counters["malformed"] == 2
    assert service.last_error.startswith(f"{PREFIX}/response: ")  # what was wrong, and where
    assert [m.payload.get("n") for m in received if m.type == "telemetry"] == [1]
    assert service.request("PING").payload["pong"] is True  # still working


def test_no_answer_times_out_with_a_clear_error(service):
    started = time.monotonic()
    with pytest.raises(MqttError, match="ไม่มีอุปกรณ์ตอบ"):
        service.request("PING", timeout_s=0.5)
    assert 0.4 < time.monotonic() - started < 2.0


def test_broker_not_running_is_an_error_not_connected():
    port = free_port()
    service = MqttService(config(port))
    service.start()
    try:
        assert wait_for(lambda: service.status.status is MqttStatus.ERROR, 5.0), service.status
        assert not service.is_connected
        with pytest.raises(MqttError):
            service.request("PING")
        broker = LocalBroker(port)  # the broker comes up later: connects by itself
        broker.start()
        try:
            assert wait_for(lambda: service.is_connected, 8.0), service.status
        finally:
            service.stop()
            broker.stop()
    finally:
        service.stop()


def test_reconnects_after_the_broker_goes_away_and_comes_back(broker, service, device):
    assert service.request("PING").payload["pong"] is True
    broker.stop()
    assert wait_for(lambda: not service.is_connected, 5.0)
    assert service.status.status in (MqttStatus.CONNECTING, MqttStatus.ERROR)
    with pytest.raises(MqttError):
        service.request("PING")

    broker.start()  # same port
    assert wait_for(lambda: service.is_connected, 10.0), service.status
    assert device.connected.wait(10.0)
    assert wait_for(lambda: _answers(service), 5.0)


@pytest.fixture
def qt_app():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def _app_settings(tmp_path, **extra):
    environ = {
        "DATA_DIR": str(tmp_path / "data"), "LOG_DIR": str(tmp_path / "logs"),
        "MEDIA_DIR": str(tmp_path / "media"), "STT_ENABLED": "false", "VISION_ENABLED": "false",
        "TTS_ENABLED": "false", **extra,
    }
    return load_settings(environ=environ)


def _pump_until(qt_app, condition, timeout_s: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while not condition():
        if time.monotonic() >= deadline:
            return False
        qt_app.processEvents()
        time.sleep(0.02)
    return True


def test_app_without_mqtt_says_so_and_never_connects(qt_app, tmp_path):
    from app.application import Application
    from ui.log_bridge import QtLogHandler

    application = Application(_app_settings(tmp_path), QtLogHandler())
    try:
        assert application.mqtt is None
        card = application.window.admin_screen._dashboard._mqtt_card
        assert card.value.text() == "ปิดใช้งาน"
    finally:
        application.shutdown()
        application.window.deleteLater()


def test_app_shows_the_broker_link_apart_from_the_robot(qt_app, tmp_path, broker):
    from app.application import Application
    from ui.log_bridge import QtLogHandler

    settings = _app_settings(tmp_path, MQTT_ENABLED="true", MQTT_PORT=str(broker.port))
    application = Application(settings, QtLogHandler())
    simulator = DeviceSimulator(port=broker.port, prefix=PREFIX)
    try:
        card = application.window.admin_screen._dashboard._mqtt_card
        robot_before = application.robot.status
        assert _pump_until(qt_app, lambda: card.value.text() == "เชื่อมต่อ broker แล้ว")
        assert "ไม่ได้หมายความว่าหุ่นยนต์เชื่อมต่อแล้ว" in card.note.text()
        assert application.robot.status == robot_before  # the broker is not the robot

        simulator.start()
        assert simulator.connected.wait(5.0)
        assert application.mqtt.service.request("PING").payload["pong"] is True
        assert _pump_until(qt_app, lambda: "อุปกรณ์จำลอง" in card.note.text())

        broker.stop()
        assert _pump_until(qt_app, lambda: card.value.text() in ("กำลังเชื่อมต่อ", "เชื่อมต่อไม่ได้"))
    finally:
        simulator.stop()
        application.shutdown()
        application.window.deleteLater()
    assert application.mqtt.status.status is MqttStatus.DISCONNECTED


def _answers(service: MqttService) -> bool:
    try:
        return service.request("PING", timeout_s=0.5).payload.get("pong") is True
    except MqttError:
        return False


def test_stop_says_offline_and_releases_the_connection(broker):
    raw = RawSubscriber(broker.port, f"{PREFIX}/status/#")
    service = MqttService(config(broker.port))
    service.start()
    try:
        assert wait_for(lambda: service.is_connected, 5.0)
        service.stop()
        service.stop()  # twice is harmless
        assert service.status.status is MqttStatus.DISCONNECTED
        assert wait_for(lambda: any(
            topic == f"{PREFIX}/status/prrobot-app-test"  # its own status topic
            and json.loads(payload)["payload"] == {"online": False, "role": "app"}
            for topic, payload in raw.messages
        ), 3.0)
        with pytest.raises(MqttError):
            service.request("PING")
    finally:
        raw.close()
