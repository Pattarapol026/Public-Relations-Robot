"""The Admin dashboard's MQTT control panel, pressed in the real app against a real broker and the simulator.

Broker (amqtt on 127.0.0.1) and simulator speak real MQTT; the app runs offscreen
with a scratch database. Every PASS here waits for the device's actual reply.
"""
from __future__ import annotations

import time

import pytest
from PySide6.QtWidgets import QApplication

from app.application import Application
from app.mqtt_controller import Outcome
from config.settings import load_settings
from tools.mqtt_broker import LocalBroker, free_port
from tools.mqtt_device_simulator import DeviceSimulator
from ui.log_bridge import QtLogHandler


@pytest.fixture(scope="module")
def qt_app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def broker():
    broker = LocalBroker(free_port())
    broker.start()
    yield broker
    broker.stop()


@pytest.fixture
def device(broker):
    simulator = DeviceSimulator(port=broker.port, telemetry_s=0)
    simulator.start()
    assert simulator.connected.wait(5.0)
    yield simulator
    simulator.stop()


def make_app(tmp_path, port: int, **extra) -> Application:
    settings = load_settings(environ={
        "DATA_DIR": str(tmp_path / "data"), "LOG_DIR": str(tmp_path / "logs"), "MEDIA_DIR": str(tmp_path / "media"),
        "STT_ENABLED": "false", "TTS_ENABLED": "false", "VISION_ENABLED": "false",
        "MQTT_ENABLED": "true", "MQTT_PORT": str(port), "MQTT_RESPONSE_TIMEOUT_S": "1", **extra,
    })
    return Application(settings, QtLogHandler())


@pytest.fixture
def app(qt_app, tmp_path, broker, device):
    application = make_app(tmp_path, broker.port)
    yield application
    application.shutdown()
    application.window.deleteLater()


def pump_until(qt_app, condition, timeout_s: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while not condition():
        if time.monotonic() > deadline:
            return False
        qt_app.processEvents()
        time.sleep(0.01)
    return True


def panel_of(application):
    return application.window.admin_screen._dashboard.mqtt_panel


def ready(qt_app, application):
    panel = panel_of(application)
    assert pump_until(qt_app, lambda: panel.broker_tag.text() == "Broker: Connected"), panel.broker_tag.text()
    assert pump_until(qt_app, lambda: panel.device_tag.text() == "อุปกรณ์: Online (SIMULATOR)"), panel.device_tag.text()
    return panel


def test_broker_and_device_are_shown_separately(qt_app, app):
    panel = ready(qt_app, app)
    assert panel.work_tag.text() == "งาน: IDLE"
    assert panel.ping_button.isEnabled() and panel.speak_button.isEnabled() and panel.stop_button.isEnabled()


def test_ping_shows_waiting_until_the_real_pong_arrives(qt_app, app, device):
    panel = ready(qt_app, app)
    started = time.monotonic()
    panel.ping_button.click()
    assert time.monotonic() - started < 0.2  # the click returns at once: the GUI is not waiting
    assert panel.result_label.text() == "รอคำตอบ..."  # no success before the reply
    assert not panel.ping_button.isEnabled()  # one command at a time
    assert pump_until(qt_app, lambda: panel.result_label.text().startswith("สำเร็จ"))
    assert "SIMULATOR prrobot-sim-device" in panel.result_label.text() and "'pong': True" in panel.result_label.text()
    assert panel.sent_label.text().startswith("คำสั่งล่าสุด: PING · ส่งเมื่อ ")
    assert device.handled == ["PING"] and panel.ping_button.isEnabled()


def test_speak_reaches_the_simulator_and_goes_busy_then_idle(qt_app, app, device):
    panel = ready(qt_app, app)
    panel.speak_text.setText("ทดสอบ SPEAK ภาษาไทย and English")
    seen = []
    app.mqtt.message_received.connect(lambda _m: seen.append(panel.work_tag.text()))
    panel.speak_button.click()
    assert pump_until(qt_app, lambda: "'accepted': True" in panel.result_label.text())
    assert panel.sent_label.text().startswith('คำสั่งล่าสุด: SPEAK "ทดสอบ SPEAK ภาษาไทย and English"')
    assert pump_until(qt_app, lambda: panel.work_tag.text() == "งาน: BUSY")
    assert pump_until(qt_app, lambda: panel.work_tag.text() == "งาน: IDLE", 6.0)
    assert device.handled == ["SPEAK"]


def test_stop_asks_first_then_stops_the_simulated_work(qt_app, app, device):
    panel = ready(qt_app, app)
    panel.confirm_stop = lambda: False
    panel.stop_button.click()
    qt_app.processEvents()
    assert app.mqtt.last_command is None  # declined: nothing sent

    panel.speak_text.setText("ข้อความยาว " * 30)
    panel.speak_button.click()
    assert pump_until(qt_app, lambda: panel.work_tag.text() == "งาน: BUSY")
    pump_until(qt_app, lambda: not app.mqtt.busy)
    panel.confirm_stop = lambda: True
    panel.stop_button.click()
    assert pump_until(qt_app, lambda: "'stopped_task': 'SPEAK'" in panel.result_label.text())
    assert pump_until(qt_app, lambda: panel.work_tag.text() == "งาน: IDLE")
    assert device.handled == ["SPEAK", "STOP"]


def test_empty_text_and_quick_double_clicks_are_handled(qt_app, app, device):
    panel = ready(qt_app, app)
    panel.speak_text.setText("   ")
    panel.speak_button.click()
    assert "ใส่ข้อความ" in panel.result_label.text() and app.mqtt.last_command is None
    assert panel.speak_text.maxLength() == 500

    app.mqtt.send("PING")
    second = app.mqtt.send("PING")  # a second click before the answer
    assert second.outcome is Outcome.FAILED and "รอคำตอบ" in second.detail
    assert pump_until(qt_app, lambda: app.mqtt.last_command.outcome is Outcome.OK)
    time.sleep(0.3)
    assert device.handled == ["PING"]  # sent once


def test_timeout_when_the_simulator_is_gone(qt_app, app, device):
    panel = ready(qt_app, app)
    device.stop()
    assert pump_until(qt_app, lambda: panel.device_tag.text() == "อุปกรณ์: Offline (SIMULATOR)")
    panel.ping_button.click()
    assert panel.result_label.text() == "รอคำตอบ..."
    assert pump_until(qt_app, lambda: panel.result_label.text().startswith("Timeout"), 4.0)
    assert app.mqtt.last_command.outcome is Outcome.TIMEOUT


def test_broker_down_disables_the_buttons_and_forgets_the_device(qt_app, app, broker):
    panel = ready(qt_app, app)
    broker.stop()
    assert pump_until(qt_app, lambda: panel.broker_tag.text() != "Broker: Connected")
    assert panel.device_tag.text() == "อุปกรณ์: Unknown"  # no broker: nothing known about the device
    assert not panel.ping_button.isEnabled() and "ยังไม่ได้เชื่อมต่อ" in panel.ping_button.toolTip()


def test_buttons_refuse_a_broker_that_is_not_on_this_computer(qt_app, tmp_path):
    application = make_app(tmp_path, 1883, MQTT_HOST="192.0.2.10")  # TEST-NET: never reachable
    try:
        panel = panel_of(application)
        assert not panel.ping_button.isEnabled()
        assert "เฉพาะ broker ในเครื่อง" in panel.ping_button.toolTip()
        assert application.mqtt.send("PING").outcome is Outcome.FAILED
    finally:
        application.shutdown()
        application.window.deleteLater()
