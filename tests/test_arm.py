"""The arm over MQTT (10 Nov 2026): the Admin's wave / back-to-rest buttons, and the wave that goes with the L-sign
introduction to the committee.

Everything runs against the local test broker on 127.0.0.1 and a fake arm; nothing is sent to a real robot."""
from __future__ import annotations

import threading
import time

import paho.mqtt.client as mqtt
import pytest
from PySide6.QtWidgets import QApplication

from app.application import Application
from app.state import ConversationState
from config.settings import ConfigError, load_settings
from services.hardware_link import HardwareLinkError
from tools.mqtt_broker import LocalBroker, free_port
from ui.fonts import apply_app_font
from ui.log_bridge import QtLogHandler
from vision.interfaces import Sign
from vision.mock_source import MockVisionSource

ARM = "robot/arm"


@pytest.fixture(scope="module")
def qt_app():
    app = QApplication.instance() or QApplication([])
    apply_app_font(app)
    return app


class FakeArm:
    """Subscribes to every robot topic and keeps what arrived on the arm's."""

    def __init__(self, port: int) -> None:
        self.received: list[str] = []
        self.everything: list[tuple[str, str]] = []
        self.ready = threading.Event()
        self._client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="fake-arm")
        self._client.on_connect = lambda c, *_: c.subscribe("robot/#", qos=1)
        self._client.on_subscribe = lambda *_: self.ready.set()
        self._client.on_message = self._on_message
        self._client.connect("127.0.0.1", port)
        self._client.loop_start()

    def _on_message(self, _client, _userdata, message) -> None:
        text = message.payload.decode("utf-8")
        self.everything.append((message.topic, text))
        if message.topic in (ARM, "robot/pos"):  # the arm's and the poses'
            self.received.append(text)

    def stop(self) -> None:
        self._client.loop_stop()
        self._client.disconnect()


@pytest.fixture
def broker():
    broker = LocalBroker(free_port())
    broker.start()
    yield broker
    broker.stop()


@pytest.fixture
def arm(broker):
    fake = FakeArm(broker.port)
    assert fake.ready.wait(5.0)
    yield fake
    fake.stop()


def pump_until(qt_app, condition, timeout_s: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while not condition():
        if time.monotonic() > deadline:
            return False
        qt_app.processEvents()
        time.sleep(0.01)
    return True


def pump(qt_app, seconds: float) -> None:
    pump_until(qt_app, lambda: False, seconds)


def build(tmp_path, broker, camera=None, **extra) -> Application:
    settings = load_settings(environ={
        "DATA_DIR": str(tmp_path / "data"), "LOG_DIR": str(tmp_path / "logs"),
        "MEDIA_DIR": str(tmp_path / "media"), "STT_ENABLED": "false", "TTS_ENABLED": "false",
        "VISION_ENABLED": "false" if camera is None else "true",
        "MQTT_HW_HOST": "127.0.0.1", "MQTT_HW_PORT": str(broker.port), "MQTT_HW_STOP_PAYLOAD": "stop",
        "MQTT_HW_STATUS_TOPIC": "robot/r1/state", **extra,
    })
    return Application(settings, QtLogHandler(), vision_source=camera) if camera else Application(settings, QtLogHandler())


def connected(qt_app, application) -> bool:
    return pump_until(qt_app, lambda: application.hardware.link.is_connected)


# --- settings ---


def test_the_arm_has_its_own_topic_and_two_messages_that_the_env_can_change():
    settings = load_settings(environ={})
    assert (settings.hw_arm_topic, settings.hw_arm_wave_payload, settings.hw_arm_home_payload) == ("robot/arm", "wave", "home")
    settings = load_settings(environ={"MQTT_HW_ARM_TOPIC": "robot/r1/arm", "MQTT_HW_ARM_WAVE_PAYLOAD": "arm wave",
                                      "MQTT_HW_ARM_HOME_PAYLOAD": "arm rest"})
    assert (settings.hw_arm_topic, settings.hw_arm_wave_payload, settings.hw_arm_home_payload) == (
        "robot/r1/arm", "arm wave", "arm rest")


def test_the_arm_topic_may_not_be_one_the_robot_talks_on():
    with pytest.raises(ConfigError):
        load_settings(environ={"MQTT_HW_ARM_TOPIC": "robot/r1/status"})


# --- the Admin's buttons ---


def test_the_admin_buttons_send_the_wave_and_the_return_under_the_same_safety_rules(qt_app, tmp_path, broker, arm):
    application = build(tmp_path, broker)
    controller = application.hardware
    try:
        assert connected(qt_app, application)
        assert "ยืนยัน" in controller.send_arm("wave")  # the area is not confirmed
        controller.confirm_area(True)
        assert controller.send_arm("wave") == ""
        assert pump_until(qt_app, lambda: arm.received == ["2"])
        assert "รออีก" in controller.send_arm("wave")  # one moving command a second
        assert controller.send_arm("home") == ""  # going back to rest is never held back
        assert pump_until(qt_app, lambda: arm.received == ["2", "1"])
        log = [(e.actor, e.action) for e in application.content.recent_activity()]
        assert ("admin", "hardware.arm") in log
    finally:
        application.shutdown()
        application.window.deleteLater()


def test_the_admin_can_change_the_arms_topic_and_messages_and_they_are_kept(qt_app, tmp_path, broker, arm):
    application = build(tmp_path, broker)
    controller = application.hardware
    try:
        assert connected(qt_app, application)
        assert controller.set_arm("robot/r1/arm", {"wave": "arm_wave", "home": "arm_rest"}) == ""
        assert controller.set_arm("robot/r1/state", {"wave": "x"}) != ""  # a topic the robot talks on
        assert controller.set_arm("robot/arm", {"wave": ""}) != ""  # an empty message
        controller.confirm_area(True)
        assert controller.send_arm("wave") == ""
        assert pump_until(qt_app, lambda: ("robot/pos", "arm_wave") in arm.everything)  # the wave has its own topic
    finally:
        application.shutdown()
        application.window.deleteLater()
    again = build(tmp_path, broker)
    try:
        stored = again.hardware.arm
        assert (stored["topic"], stored["wave"], stored["home"]) == ("robot/r1/arm", "arm_wave", "arm_rest")
        assert stored["shake_hand"] == "shake_hand"  # a gesture nobody renamed sends its id
    finally:
        again.shutdown()
        again.window.deleteLater()


def test_the_arm_has_a_list_of_gestures_and_each_is_sent_under_its_own_message(qt_app, tmp_path, broker, arm):
    from app.hardware_controller import ARM_GESTURES

    assert [(g.menu, g.id) for g in ARM_GESTURES][:3] == [(1, "home"), (2, "wave"), (3, "wave_right")]  # the order the robot's poses came in
    application = build(tmp_path, broker)
    controller = application.hardware
    try:
        assert connected(qt_app, application)
        controller.set_arm("robot/arm", {"shake_hand": "arm shake"}, auto_return=False)
        controller.confirm_area(True)
        assert controller.send_arm("shake_hand") == ""
        assert pump_until(qt_app, lambda: arm.received == ["arm shake"])
    finally:
        application.shutdown()
        application.window.deleteLater()


def test_after_a_gesture_the_arm_is_sent_back_to_rest_by_itself_unless_that_is_switched_off(qt_app, tmp_path, broker, arm):
    application = build(tmp_path, broker)
    controller = application.hardware
    try:
        assert connected(qt_app, application)
        controller.confirm_area(True)
        controller.set_arm("robot/arm", seconds={"shake_hand": 0.5})
        assert controller.send_arm("shake_hand") == ""
        assert pump_until(qt_app, lambda: arm.received == ["shake_hand", "1"], timeout_s=4.0)  # no button for the return
        controller.set_arm("robot/arm", auto_return=False)
        pump(qt_app, 1.2)  # past the one-second pause between moving commands
        assert controller.send_arm("shake_hand") == ""
        pump(qt_app, 1.5)
        assert arm.received == ["shake_hand", "1", "shake_hand"]  # switched off: it stays as the hardware leaves it
    finally:
        application.shutdown()
        application.window.deleteLater()


def test_sending_rest_by_hand_cancels_the_automatic_return(qt_app, tmp_path, broker, arm):
    application = build(tmp_path, broker)
    controller = application.hardware
    try:
        assert connected(qt_app, application)
        controller.confirm_area(True)
        controller.set_arm("robot/arm", seconds={"wave": 0.5})
        assert controller.send_arm("wave") == "" and controller.send_arm("home") == ""
        pump(qt_app, 1.5)
        assert arm.received == ["2", "1"]  # not a second "home" from the timer
    finally:
        application.shutdown()
        application.window.deleteLater()


def test_the_poses_page_lists_the_poses_and_tests_one_under_the_same_safety_rules(qt_app, tmp_path, broker, arm):
    application = build(tmp_path, broker)
    tab = application.window.admin_screen._poses_tab
    try:
        assert connected(qt_app, application)
        pump(qt_app, 0.1)
        tab.refresh()
        titles = [tab.table.item(row, 1).text() for row in range(tab.table.rowCount())]
        assert titles[:3] == ["ท่าพัก", "โบกมือซ้าย", "โบกมือขวา"]  # menu 1, 2, 3
        assert tab.table.item(0, 2).text() == "robot/pos" and tab.table.item(2, 2).text() == "robot/pos"
        tab.select("wave_right")
        assert not tab.test_button.isEnabled() and "ยืนยัน" in tab.test_button.toolTip()
        tab.confirm_box.setChecked(True)
        assert tab.test_button.isEnabled()
        tab.test_button.click()
        assert pump_until(qt_app, lambda: ("robot/pos", "3") in arm.everything)
        assert pump_until(qt_app, lambda: ("robot/pos", "1") in arm.everything, timeout_s=6.0)  # the rest pose, by itself
        tab.rest_button.click()
        assert pump_until(qt_app, lambda: arm.everything.count(("robot/pos", "1")) >= 2)
    finally:
        application.shutdown()
        application.window.deleteLater()


def test_the_admin_adds_changes_and_deletes_poses_and_they_are_kept(qt_app, tmp_path, broker):
    from app.hardware_controller import ArmPose

    application = build(tmp_path, broker)
    controller = application.hardware
    try:
        new = ArmPose(id="hug", title="กอด", description="", seconds=5.0, topic="robot/pos-m4", phrases=("กอดหน่อย",), menu=11)
        assert controller.save_pose(new) == ""
        assert controller.pose("hug").message == "hug"  # no message set: the id is sent
        assert controller.save_pose(new) != ""  # the id is taken
        assert controller.save_pose(ArmPose(id="Bad Id", title="x", description="", seconds=1.0)) != ""
        assert controller.save_pose(ArmPose(id="x1", title="x", description="", seconds=1.0, topic="robot/r1/state")) != ""
        assert controller.save_pose(ArmPose(id="x1", title="x", description="", seconds=99.0)) != ""
        assert controller.save_pose(ArmPose(id="hug", title="กอดแน่น", description="", seconds=5.0, topic="robot/pos-m4",
                                            payload="1", menu=11), "hug") == ""  # changed
        assert controller.delete_pose("wave") != ""  # tied to the introduction
        assert controller.delete_pose("nope") != ""
    finally:
        application.shutdown()
        application.window.deleteLater()
    again = build(tmp_path, broker)
    try:
        hug = again.hardware.pose("hug")
        assert hug is not None and hug.title == "กอดแน่น" and hug.message == "1" and hug.topic == "robot/pos-m4"
        assert again.hardware.delete_pose("hug") == ""
        assert again.hardware.pose("hug") is None
    finally:
        again.shutdown()
        again.window.deleteLater()


def test_which_pose_each_event_uses_can_be_changed_and_a_pose_in_use_cannot_be_deleted(qt_app, tmp_path, broker, arm):
    application = build(tmp_path, broker)
    controller = application.hardware
    try:
        assert connected(qt_app, application)
        assert controller.events == {"intro": "wave", "rest": "home", "camera_67": "six_seven"}
        assert controller.set_event("intro", "wave_right") == "" and controller.set_event("intro", "nope") != ""
        assert controller.delete_pose("wave") != ""  # still the answer to a goodbye wave
        controller.set_gesture_pose("Wave_Visitor_Right", "")
        assert controller.delete_pose("wave_right") != "" and controller.delete_pose("wave") == ""  # no longer the introduction's
        controller.set_arm_with_intro(True)
        assert controller.intro_wave() is True  # now the left hand
        assert pump_until(qt_app, lambda: ("robot/pos", "3") in arm.everything)
        assert controller.intro_end() is True
        assert pump_until(qt_app, lambda: ("robot/pos", "1") in arm.everything)
    finally:
        application.shutdown()
        application.window.deleteLater()


def test_a_visitor_asks_for_a_pose_by_one_of_its_phrases(qt_app, tmp_path, broker, arm):
    application = build(tmp_path, broker)
    controller = application.hardware
    try:
        assert connected(qt_app, application)
        assert controller.pose_for_phrase("โบกมือซ้าย").id == "wave"
        assert controller.pose_for_phrase("ช่วยโบกมือซ้ายหน่อยครับ").id == "wave"
        assert controller.pose_for_phrase("ภาคใต้มีกี่ชิ้น") is None
        controller.set_arm_by_voice(True)
        application.conversation.ask("โบกมือซ้าย", "voice")
        assert pump_until(qt_app, lambda: ("robot/pos", "2") in arm.everything)
        screen = application.window.user_screen
        assert pump_until(qt_app, lambda: any("โบกมือซ้าย" in text for text in screen.transcript_texts()))
        controller.save_pose(replace_enabled(controller.pose("wave"), False), "wave")
        assert controller.pose_for_phrase("โบกมือซ้าย") is None  # switched off
    finally:
        application.shutdown()
        application.window.deleteLater()


def replace_enabled(pose, enabled):
    from dataclasses import replace

    return replace(pose, enabled=enabled)


# --- the wave during the introduction ---


def test_the_intro_wave_is_off_until_the_admin_turns_it_on_and_needs_no_area_confirmation(qt_app, tmp_path, broker, arm):
    application = build(tmp_path, broker)
    controller = application.hardware
    try:
        assert connected(qt_app, application)
        assert not controller.arm_with_intro and controller.intro_wave() is False
        pump(qt_app, 0.3)
        assert arm.received == []
        controller.set_arm_with_intro(True)
        assert controller.intro_wave() is True  # nobody confirmed the area: the presenter stands close on purpose
        assert controller.intro_end() is True
        assert controller.intro_end() is False  # sent once
        assert pump_until(qt_app, lambda: arm.received == ["2", "1"])
    finally:
        application.shutdown()
        application.window.deleteLater()


def test_nothing_is_sent_and_nothing_breaks_when_the_arm_is_not_connected(qt_app, tmp_path):
    from services.hardware_link import HardwareLink, HardwareLinkConfig
    from app.hardware_controller import HardwareController

    link = HardwareLink(HardwareLinkConfig(host="127.0.0.1", stop_payload="stop"))  # never started
    controller = HardwareController(link)
    controller.set_arm_with_intro(True)
    assert controller.intro_wave() is False and controller.intro_end() is False
    with pytest.raises(HardwareLinkError):
        link.send_arm("wave", "robot/arm")
    assert "เชื่อมต่อ" in controller.send_arm("wave")


@pytest.fixture
def sign_app(qt_app, tmp_path, broker):
    camera = MockVisionSource()
    application = build(tmp_path, broker, camera=camera, VISION_GREET_DWELL_SECONDS="0.1", VISION_DWELL_SECONDS="0.1")
    yield application, camera
    application.shutdown()
    application.window.deleteLater()


def test_the_l_sign_waves_the_arm_at_once_and_sends_it_back_when_the_speech_ends(qt_app, sign_app, arm):
    application, camera = sign_app
    assert connected(qt_app, application)
    application.hardware.set_arm_with_intro(True)
    camera.place_person(1.0)
    assert pump_until(qt_app, lambda: application.vision.present)
    application.conversation.stop()
    camera.show_signs(Sign("gesture", "Finger_Gun"))
    assert pump_until(qt_app, lambda: arm.received == ["2"])  # with the first words, before the speech is over
    assert application.conversation.state.state is ConversationState.SPEAKING
    pump(qt_app, 0.3)
    assert arm.received == ["2"]  # still up while it speaks
    application.conversation.stop()  # the speech ends (here: cut short, as "หยุดสนทนา" would)
    assert pump_until(qt_app, lambda: arm.received == ["2", "1"])


def test_without_the_switch_the_l_sign_does_not_touch_the_arm_and_the_speech_still_goes_on(qt_app, sign_app, arm):
    application, camera = sign_app
    assert connected(qt_app, application)
    camera.place_person(1.0)
    assert pump_until(qt_app, lambda: application.vision.present)
    application.conversation.stop()
    camera.show_signs(Sign("gesture", "Finger_Gun"))
    assert pump_until(qt_app, lambda: application.conversation.state.state is ConversationState.SPEAKING)
    pump(qt_app, 0.4)
    assert arm.received == []


def test_the_speech_goes_on_when_the_arm_message_cannot_be_sent(qt_app, tmp_path, broker):
    camera = MockVisionSource()
    application = build(tmp_path, broker, camera=camera, VISION_GREET_DWELL_SECONDS="0.1", VISION_DWELL_SECONDS="0.1")
    try:
        assert connected(qt_app, application)
        application.hardware.set_arm_with_intro(True)
        application.hardware.link.send_arm = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("broker gone"))
        camera.place_person(1.0)
        assert pump_until(qt_app, lambda: application.vision.present)
        application.conversation.stop()
        camera.show_signs(Sign("gesture", "Finger_Gun"))
        assert pump_until(qt_app, lambda: application.conversation.state.state is ConversationState.SPEAKING)
        assert application.window._arm_waving is False  # nothing was raised, so nothing to bring back
    finally:
        application.shutdown()
        application.window.deleteLater()


def test_the_arm_is_not_left_up_if_the_speech_never_starts(qt_app, sign_app, arm, monkeypatch):
    import ui.main_window as main_window

    application, camera = sign_app
    assert connected(qt_app, application)
    application.hardware.set_arm_with_intro(True)
    application.window._begin_arm_wave()
    assert pump_until(qt_app, lambda: arm.received == ["2"])
    application.window._arm_check_started()  # the guard timer's moment: no speech began
    assert pump_until(qt_app, lambda: arm.received == ["2", "1"])
    assert main_window.ARM_WAVE_GESTURES == ("Finger_Gun",)


# --- the "67" gesture ---


@pytest.mark.parametrize("text", ["67", "ทำท่า 67", "six seven", "Six, seven!", "หกสิบเจ็ด", "6 7 ครับ", "ซิกซ์เซเว่น"])
def test_the_phrases_that_ask_for_the_67_gesture(text):
    from services.arm_phrases import SIX_SEVEN, gesture_for

    assert gesture_for(text) == SIX_SEVEN


@pytest.mark.parametrize("text", ["ขอนแก่นส่ง 67 ชิ้น", "ภาคใต้ 14 ชิ้น", "ปีนี้มี 67 ผลงานไหม", "ห้อง 933 อยู่ไหน"])
def test_a_sentence_that_only_holds_the_number_is_still_a_question(text):
    from services.arm_phrases import gesture_for

    assert gesture_for(text) is None


def test_the_67_gesture_is_in_the_list_and_swings_the_arms_in_turn():
    from app.hardware_controller import ARM_GESTURES

    gesture = next(g for g in ARM_GESTURES if g.id == "six_seven")
    assert "สลับกัน" in gesture.description and gesture.seconds > 0


def test_saying_67_makes_the_arm_swing_and_the_robot_says_six_seven_once(qt_app, tmp_path, broker, arm):
    application = build(tmp_path, broker)
    screen = application.window.user_screen
    try:
        assert connected(qt_app, application)
        application.hardware.set_arm_by_voice(True)
        application.hardware.set_arm("robot/arm", seconds={"six_seven": 0.5})
        application.conversation.ask("67", "voice")
        assert pump_until(qt_app, lambda: arm.received[:1] == ["six_seven"])
        assert [t for t in screen.transcript_texts() if "Six seven" in t] == ["หุ่นยนต์: Six seven."]  # said once
        assert pump_until(qt_app, lambda: arm.received == ["six_seven", "1"], timeout_s=4.0)  # and back to rest
    finally:
        application.shutdown()
        application.window.deleteLater()


def test_without_the_switch_the_robot_says_six_seven_but_the_arm_does_not_move(qt_app, tmp_path, broker, arm):
    application = build(tmp_path, broker)
    screen = application.window.user_screen
    try:
        assert connected(qt_app, application)
        assert not application.hardware.arm_by_voice
        application.conversation.ask("six seven", "voice")
        assert pump_until(qt_app, lambda: any("Six seven" in t for t in screen.transcript_texts()))
        pump(qt_app, 0.4)
        assert arm.received == []
    finally:
        application.shutdown()
        application.window.deleteLater()


def test_a_question_with_67_in_it_is_answered_and_the_arm_stays_still(qt_app, tmp_path, broker, arm):
    application = build(tmp_path, broker)
    try:
        assert connected(qt_app, application)
        application.hardware.set_arm_by_voice(True)
        application.conversation.ask("ขอนแก่นส่ง 67 ชิ้นใช่ไหม", "voice")
        pump(qt_app, 0.8)
        assert arm.received == []
    finally:
        application.shutdown()
        application.window.deleteLater()


def test_a_visitor_doing_67_in_front_of_the_camera_makes_the_arm_do_it_and_the_robot_say_six_seven(qt_app, sign_app, arm):
    """10 Nov 2026: the camera sees two hands swinging in turn: the robot says "Six seven." and its arms do the same."""
    application, camera = sign_app
    screen = application.window.user_screen
    assert connected(qt_app, application)
    application.hardware.set_arm_by_voice(True)
    application.hardware.set_arm("robot/arm", seconds={"six_seven": 0.5})
    camera.place_person(1.0)
    assert pump_until(qt_app, lambda: application.vision.present)
    application.conversation.stop()
    camera.show_signs(Sign("gesture", "Six_Seven"))
    assert pump_until(qt_app, lambda: arm.received[:1] == ["six_seven"])
    assert pump_until(qt_app, lambda: any("Six seven." in text for text in screen.transcript_texts()))
    assert screen.face_mood() == "groove"
    assert pump_until(qt_app, lambda: arm.received == ["six_seven", "1"], timeout_s=4.0)


def test_a_gesture_can_have_a_topic_of_its_own_like_the_robots_pose_topics(qt_app, tmp_path, broker, arm):
    application = build(tmp_path, broker)
    controller = application.hardware
    try:
        assert connected(qt_app, application)
        assert controller.set_arm("robot/arm", {"wave": "go"}, topics={"wave": "robot/pos-m1", "shake_hand": "robot/pos-m2"}) == ""
        assert controller.set_arm("robot/arm", topics={"wave": "robot/r1/state"}) != ""  # one the robot talks on
        controller.confirm_area(True)
        assert controller.send_arm("wave") == ""
        assert pump_until(qt_app, lambda: ("robot/pos-m1", "go") in arm.everything)
        assert controller.arm_topic_for("raise") == "robot/arm"  # no topic of its own: the arm's
        assert controller.arm_topic_for("shake_hand") == "robot/pos-m2"
    finally:
        application.shutdown()
        application.window.deleteLater()
    again = build(tmp_path, broker)
    try:
        assert again.hardware.arm_topics["wave"] == "robot/pos-m1"
    finally:
        again.shutdown()
        again.window.deleteLater()


def test_the_robots_three_poses_go_to_their_own_topics_by_default(qt_app, tmp_path, broker, arm):
    """From the hardware team: mosquitto_pub -t robot/pos -m 1 is the rest pose, -m 2 waves the left hand; -m 3 for the right is a guess."""
    application = build(tmp_path, broker)
    controller = application.hardware
    try:
        assert (controller.arm_topic_for("home"), controller.arm_topic_for("wave"), controller.arm_topic_for("wave_right")) == (
            "robot/pos", "robot/pos", "robot/pos")
        assert connected(qt_app, application)
        controller.confirm_area(True)
        assert controller.send_arm("wave_right") == ""
        assert pump_until(qt_app, lambda: ("robot/pos", "3") in arm.everything)
    finally:
        application.shutdown()
        application.window.deleteLater()


def test_the_add_and_edit_form_saves_a_pose_and_shows_why_not(qt_app, tmp_path, broker, monkeypatch):
    import ui.admin_poses as admin_poses

    application = build(tmp_path, broker)
    tab = application.window.admin_screen._poses_tab
    typed = {}

    class FakeDialog:
        def __init__(self, title, fields, submit, error_type, parent=None):
            self.fields, self.submit, self.error_type = {f.key: f.value for f in fields}, submit, error_type

        def exec(self):
            values = {**self.fields, **typed}
            try:
                self.submit(values)
            except self.error_type as exc:
                self.problem = str(exc)
                FakeDialog.problem = str(exc)
                return 0
            return 1

    monkeypatch.setattr(admin_poses, "FormDialog", FakeDialog)
    try:
        typed.update(title="กอด", id="hug", topic="robot/pos-m4", seconds="5", phrases="กอดหน่อย, กอดที", menu="11")
        tab.add_pose()
        assert tab.hardware.pose("hug").phrases == ("กอดหน่อย", "กอดที") and tab.selected_id() == "hug"
        typed.update(id="wave")  # taken
        tab.add_pose()
        assert "มีท่ารหัส wave" in FakeDialog.problem
        typed.update(id="hug2", seconds="abc")
        tab.add_pose()
        assert "ตัวเลข" in FakeDialog.problem
        tab.select("hug")
        typed.clear()
        typed.update(title="กอดแน่น")
        tab.edit_selected()
        assert tab.hardware.pose("hug").title == "กอดแน่น"
        tab.delete_selected()
        assert tab.hardware.pose("hug") is None
    finally:
        application.shutdown()
        application.window.deleteLater()


def test_the_mouse_wheel_does_not_change_which_pose_an_event_uses(qt_app, tmp_path, broker):
    """11 Nov 2026: scrolling the page over the list ran the introduction's pose through eight poses."""
    from PySide6.QtCore import QPoint, QPointF, Qt
    from PySide6.QtGui import QWheelEvent

    application = build(tmp_path, broker)
    tab = application.window.admin_screen._poses_tab
    try:
        box = tab.event_boxes["intro"]
        before = application.hardware.events["intro"]
        event = QWheelEvent(QPointF(5, 5), QPointF(5, 5), QPoint(0, 0), QPoint(0, -120), Qt.MouseButton.NoButton,
                            Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False)
        QApplication.sendEvent(box, event)
        assert box.currentData() == before and application.hardware.events["intro"] == before
    finally:
        application.shutdown()
        application.window.deleteLater()


def test_a_phrase_asked_for_while_the_voice_switch_is_off_is_logged_not_silently_dropped(qt_app, tmp_path, broker, arm):
    application = build(tmp_path, broker)
    try:
        assert connected(qt_app, application)
        assert not application.hardware.arm_by_voice
        application.conversation.ask("โบกมือซ้าย", "voice")
        assert pump_until(qt_app, lambda: any(e.action == "hardware.arm_voice" and "switch off" in e.detail
                                              for e in application.content.recent_activity()))
        assert arm.received == []
    finally:
        application.shutdown()
        application.window.deleteLater()


# --- the camera sees a hand and the arm answers (11 Nov 2026) ---


def test_the_defaults_for_what_the_arm_does_when_the_camera_sees_a_hand(qt_app, tmp_path, broker):
    controller = build(tmp_path, broker).hardware
    try:
        assert controller.pose_for_gesture("Victory") == "six_seven"  # two fingers up: the 67 pose
        assert controller.pose_for_gesture("Wave_Visitor_Right") == "wave"  # the visitor's right hand: the robot's left
        assert controller.pose_for_gesture("Wave_Visitor_Left") == "wave_right"
        assert controller.pose_for_gesture("Thumb_Up") == ""
        assert controller.set_gesture_pose("Thumb_Up", "cheer") == "" and controller.pose_for_gesture("Thumb_Up") == "cheer"
        assert controller.set_gesture_pose("Thumb_Up", "nope") != ""
        assert controller.set_gesture_pose("Victory", "") == "" and controller.pose_for_gesture("Victory") == ""
        assert "ท่ามือ Thumb_Up" in controller.uses_of("cheer") and controller.delete_pose("cheer") != ""
    finally:
        controller.shutdown()


@pytest.mark.parametrize("sign, message", [("Wave_Visitor_Right", "2"), ("Wave_Visitor_Left", "3"), ("Victory", "six_seven")])
def test_a_hand_the_camera_sees_makes_the_arm_do_the_pose_tied_to_it(qt_app, sign_app, arm, sign, message):
    application, camera = sign_app
    assert connected(qt_app, application)
    application.hardware.set_arm_by_voice(True)
    application.hardware.set_arm("robot/arm", seconds={"wave": 0.5, "wave_right": 0.5, "six_seven": 0.5})
    camera.place_person(1.0)
    assert pump_until(qt_app, lambda: application.vision.present)
    application.conversation.stop()
    camera.show_signs(Sign("gesture", sign))
    assert pump_until(qt_app, lambda: arm.received[:1] == [message])
    assert pump_until(qt_app, lambda: arm.received[-1:] == ["1"], timeout_s=4.0)  # and back to the rest pose


def test_the_poses_page_has_a_row_for_each_hand_the_camera_can_answer(qt_app, tmp_path, broker):
    application = build(tmp_path, broker)
    tab = application.window.admin_screen._poses_tab
    try:
        box = tab.gesture_boxes["Wave_Visitor_Right"]
        assert box.currentData() == "wave" and tab.gesture_boxes["Thumb_Up"].currentData() == ""
        box.setCurrentIndex(box.findData("wave_right"))
        tab._on_gesture_pose("Wave_Visitor_Right")
        assert application.hardware.pose_for_gesture("Wave_Visitor_Right") == "wave_right"
    finally:
        application.shutdown()
        application.window.deleteLater()
