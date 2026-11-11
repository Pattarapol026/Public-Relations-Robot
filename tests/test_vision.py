"""Presence detection logic, and auto-greeting through the real application with a mock camera."""
from __future__ import annotations

import dataclasses
import time

import pytest
from PySide6.QtCore import QBuffer, QIODevice
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication

from app.application import Application
from app.content_controller import HOME_CAMERA, HOME_MEDIA, ContentError
from app.state import ConversationState
from config.settings import ConfigError, load_settings
from fakes import FakeMicrophone, FakeStt
from ui.fonts import apply_app_font
from ui.log_bridge import QtLogHandler
from ui.user_screen import UserScreen
from vision.interfaces import PersonObservation, SourceStatus
from vision.mock_source import MockVisionSource
from vision.presence import PresenceConfig, PresenceEventType, PresenceTracker

CONFIG = PresenceConfig(
    engage_distance_m=1.8, release_distance_m=2.5, lateral_limit_m=1.0,
    dwell_s=0.8, leave_s=4.0, smoothing_samples=5,
)
STEP = 0.1


def person(distance: float, x: float = 0.0, confidence: float = 0.9) -> PersonObservation:
    return PersonObservation(x_m=x, z_m=distance, confidence=confidence)


class Timeline:
    """Feeds a tracker at 10 Hz and collects the events."""

    def __init__(self, config: PresenceConfig = CONFIG) -> None:
        self.tracker = PresenceTracker(config)
        self.now = 100.0
        self.events = []

    def feed(self, seconds: float, *people: PersonObservation) -> list:
        new = []
        for _ in range(round(seconds / STEP)):
            self.now += STEP
            new += self.tracker.update(people, self.now)
        self.events += new
        return new

    @property
    def types(self) -> list[PresenceEventType]:
        return [event.type for event in self.events]


# --- presence logic ---


def test_person_who_stops_in_front_arrives_once():
    timeline = Timeline()
    assert timeline.feed(2.0) == []
    timeline.feed(3.0, person(1.2))
    assert timeline.types == [PresenceEventType.ARRIVED]
    assert timeline.events[0].distance_m == pytest.approx(1.2)
    assert timeline.tracker.present


def test_person_walking_past_at_a_distance_or_to_the_side_is_ignored():
    timeline = Timeline()
    timeline.feed(5.0, person(3.5))  # too far
    timeline.feed(5.0, person(1.0, x=1.6))  # close, but off to the side
    timeline.feed(5.0, person(1.0, confidence=0.2))  # not a confident detection
    timeline.feed(5.0, person(0.1))  # closer than stereo depth can measure
    assert timeline.events == []


def test_someone_crossing_quickly_does_not_trigger_a_greeting():
    timeline = Timeline()
    timeline.feed(0.5, person(1.0))  # in range for less than smoothing + dwell
    timeline.feed(3.0)
    assert timeline.events == []


def test_single_frame_glitches_are_rejected():
    timeline = Timeline()
    for _ in range(10):  # one stray detection every second
        timeline.feed(0.1, person(1.0))
        timeline.feed(0.9)
    assert timeline.events == []

    # ...and a visitor is not lost when the detector misses them for single frames.
    timeline.feed(2.0, person(1.0))
    for _ in range(20):
        timeline.feed(0.1)
        timeline.feed(0.4, person(1.0))
    assert timeline.types == [PresenceEventType.ARRIVED]


def test_visitor_leaves_after_being_gone_long_enough():
    timeline = Timeline()
    timeline.feed(3.0, person(1.5))
    timeline.feed(5.0, person(0.8))  # steps closer
    timeline.feed(2.0)  # looks away / detector loses them briefly
    timeline.feed(2.0, person(1.0))
    assert timeline.types == [PresenceEventType.ARRIVED]  # a short gap is not leaving

    timeline.feed(6.0)
    assert timeline.types == [PresenceEventType.ARRIVED, PresenceEventType.LEFT]
    left = timeline.events[1]
    assert left.closest_m == pytest.approx(0.8)
    assert 10.0 < left.duration_s < 13.0  # measured to when they were last seen
    assert not timeline.tracker.present


def test_hysteresis_between_engage_and_release_distance():
    timeline = Timeline()
    timeline.feed(3.0, person(2.2))  # between the two distances: not yet a visitor
    assert timeline.events == []
    timeline.feed(3.0, person(1.5))
    timeline.feed(10.0, person(2.2))  # stepped back a little: still the same visit
    assert timeline.types == [PresenceEventType.ARRIVED]
    timeline.feed(6.0, person(3.0))  # walked away but still visible
    assert timeline.types == [PresenceEventType.ARRIVED, PresenceEventType.LEFT]


def test_nearest_of_several_people_is_used_and_second_visit_is_a_new_arrival():
    timeline = Timeline()
    timeline.feed(3.0, person(3.0), person(1.4), person(2.0, x=0.5))
    assert timeline.events[0].distance_m == pytest.approx(1.4)
    assert timeline.tracker.people_count == 3
    timeline.feed(6.0)
    timeline.feed(3.0, person(1.0))
    assert timeline.types == [
        PresenceEventType.ARRIVED, PresenceEventType.LEFT, PresenceEventType.ARRIVED,
    ]


@pytest.mark.parametrize(
    "environ",
    [
        {"VISION_ENGAGE_DISTANCE_M": "4"},  # talk ring outside the greeting ring
        {"VISION_GREET_DISTANCE_M": "5"},  # greeting ring outside the "gone" ring
        {"VISION_RELEASE_DISTANCE_M": "1"},
    ],
)
def test_camera_distances_must_be_ordered_from_near_to_far(environ):
    with pytest.raises(ConfigError):
        load_settings(environ=environ)


# --- auto-greeting in the application ---


def greeting_now(application) -> str:
    """What the robot says to an arriving visitor now: the greeting of the place in use."""
    from datetime import datetime

    return application.content.greeting_at(datetime.now())


@pytest.fixture(scope="module")
def qt_app():
    qt_app = QApplication.instance() or QApplication([])
    apply_app_font(qt_app)
    return qt_app


def make_settings(tmp_path, **extra):
    return load_settings(
        environ={
            "DATA_DIR": str(tmp_path / "data"),
            "LOG_DIR": str(tmp_path / "logs"),
            "MEDIA_DIR": str(tmp_path / "media"),
            "STT_ENABLED": "false",
            "TTS_ENABLED": "false",
            "VISION_DWELL_SECONDS": "0.2",
            "VISION_GREET_DWELL_SECONDS": "0.3", "VISION_GREET_STILL_SECONDS": "0",
            "VISION_LEAVE_SECONDS": "0.4",
            **extra,
        }
    )


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


@pytest.fixture
def vision_app(qt_app, tmp_path):
    camera = MockVisionSource()
    application = Application(make_settings(tmp_path), QtLogHandler(), vision_source=camera)
    yield application, camera
    application.shutdown()
    application.window.deleteLater()


def test_robot_greets_first_and_the_conversation_goes_on_when_the_camera_loses_the_visitor(qt_app, vision_app):
    """Changed 9 Nov 2026: the camera not seeing anyone any more does not end the conversation (only "หยุดสนทนา" does)."""
    application, camera = vision_app
    screen = application.window.user_screen
    greeting = greeting_now(application)
    screen.show_page(UserScreen.PAGE_HOME)

    camera.place_person(1.2)
    wait_until(qt_app, lambda: application.vision.present)
    assert screen.current_page() == UserScreen.PAGE_HOME  # greeted, but the page is not switched
    assert screen.transcript_texts() == [f"หุ่นยนต์: {greeting}"]

    application.conversation.ask(application.settings.faq[0].question)  # asking opens the chat page
    wait_until(qt_app, lambda: len(screen.transcript_texts()) == 3)
    assert screen.current_page() == UserScreen.PAGE_CHAT

    states = []
    application.conversation.state.state_changed.connect(lambda _old, new: states.append(new))
    camera.clear()
    wait_until(qt_app, lambda: not application.vision.present)
    pump(qt_app, 0.5)
    assert len(screen.transcript_texts()) == 3  # nothing is cleared, nothing is said as a farewell
    assert screen.current_page() == UserScreen.PAGE_CHAT
    assert application.conversation.session_active or not screen.listening_paused  # and it is not stopped
    assert ConversationState.SPEAKING not in states  # no farewell

    actions = [(e.actor, e.action) for e in application.content.recent_activity()]
    assert ("vision", "visitor.arrived") in actions and ("vision", "visitor.left") in actions


def test_greeting_text_is_editable_in_admin(qt_app, vision_app):
    application, camera = vision_app
    application.content.save_display_settings("ยินดีต้อนรับ", 8, 8, "สวัสดีค่ะ ให้ช่วยอะไรดีคะ")
    camera.place_person(1.0)
    wait_until(qt_app, lambda: application.vision.present)
    assert application.window.user_screen.transcript_texts() == ["หุ่นยนต์: สวัสดีค่ะ ให้ช่วยอะไรดีคะ"]


def test_camera_does_not_disturb_admin_or_a_conversation_in_progress(qt_app, vision_app):
    application, camera = vision_app
    window = application.window
    screen = window.user_screen

    window.enter_admin()
    camera.place_person(1.0)
    wait_until(qt_app, lambda: application.vision.present)
    assert window.in_admin_mode()
    assert screen.transcript_texts() == []
    camera.clear()
    wait_until(qt_app, lambda: not application.vision.present)
    assert window.in_admin_mode()  # the Admin stepping away from the camera is not logged out by it
    window.leave_admin()

    # A visitor already talking via the touch screen is not interrupted by a greeting.
    application.conversation.ask(application.settings.faq[0].question)
    camera.place_person(1.0)
    wait_until(qt_app, lambda: application.vision.present)
    assert screen.transcript_texts()[0].startswith("คุณ:")


def test_idle_timeout_spares_a_visitor_who_is_still_there_but_logs_admin_out(qt_app, vision_app):
    application, camera = vision_app
    window = application.window
    camera.place_person(1.0)
    wait_until(qt_app, lambda: application.vision.present)
    window._on_idle()
    assert window.user_screen.transcript_texts() != []  # still reading: left alone

    window._settings = dataclasses.replace(window._settings, admin_idle_timeout_s=600)
    window.enter_admin()
    assert window._idle_timer.interval() == 600_000
    window._on_idle()
    assert not window.in_admin_mode()


def test_stalled_camera_releases_the_visitor(qt_app, vision_app):
    application, camera = vision_app
    camera.place_person(1.0)
    wait_until(qt_app, lambda: application.vision.present)
    camera.freeze()  # keeps reporting the same old frame with a person in it
    wait_until(qt_app, lambda: not application.vision.present, timeout_s=5.0)


def test_arrival_starts_listening_hands_free_when_voice_is_ready(qt_app, tmp_path):
    camera, microphone = MockVisionSource(), FakeMicrophone()
    application = Application(
        make_settings(tmp_path, STT_ENABLED="true", MIC_NO_SPEECH_TIMEOUT_S="30"),
        QtLogHandler(), microphone=microphone, stt=FakeStt(), vision_source=camera,
    )
    try:
        wait_until(qt_app, lambda: application.voice.is_listening is False
                   and application.voice.status.value == "ready")
        application.window.user_screen.show_page(UserScreen.PAGE_CHAT)  # hands-free needs the chat page open
        camera.place_person(1.0)
        wait_until(qt_app, lambda: application.vision.engaged)
        assert application.conversation.state.state is ConversationState.SPEAKING  # greeting first
        wait_until(qt_app, lambda: microphone.recording, timeout_s=8.0)  # then listens by itself
        assert application.conversation.state.state is ConversationState.LISTENING

        camera.clear()  # walks away without saying anything
        wait_until(qt_app, lambda: not application.vision.present)
        pump(qt_app, 0.6)
        assert microphone.recording  # still listening: the camera losing them does not end the conversation
    finally:
        application.shutdown()
        application.window.deleteLater()


def test_a_visitor_who_steps_up_is_greeted_and_heard_on_the_conversation_page(qt_app, tmp_path):
    camera, microphone = MockVisionSource(), FakeMicrophone()
    application = Application(
        make_settings(tmp_path, STT_ENABLED="true", MIC_NO_SPEECH_TIMEOUT_S="30"),
        QtLogHandler(), microphone=microphone, stt=FakeStt(), vision_source=camera,
    )
    try:
        wait_until(qt_app, lambda: application.voice.status.value == "ready")
        screen = application.window.user_screen
        assert screen.current_page() == UserScreen.PAGE_CHAT  # the only page shown
        camera.place_person(1.0)
        wait_until(qt_app, lambda: application.vision.engaged)
        wait_until(qt_app, lambda: microphone.recording, timeout_s=8.0)  # hands-free, after the greeting
        assert screen.current_page() == UserScreen.PAGE_CHAT
        assert application.conversation.session_active  # so the answer is spoken
        assert screen.transcript_texts() == [f"หุ่นยนต์: {greeting_now(application)}"]
    finally:
        application.shutdown()
        application.window.deleteLater()


def test_auto_listen_can_be_turned_off(qt_app, tmp_path):
    camera, microphone = MockVisionSource(), FakeMicrophone()
    application = Application(
        make_settings(tmp_path, STT_ENABLED="true", VISION_AUTO_LISTEN="false"),
        QtLogHandler(), microphone=microphone, stt=FakeStt(), vision_source=camera,
    )
    try:
        wait_until(qt_app, lambda: application.voice.status.value == "ready")
        camera.place_person(1.0)
        wait_until(qt_app, lambda: application.vision.engaged)
        finished = []
        application.conversation.turn_finished.connect(finished.append)
        wait_until(qt_app, lambda: finished, timeout_s=8.0)  # the greeting has been spoken
        pump(qt_app, 0.3)
        assert finished == [False] and not microphone.recording
        assert application.window.user_screen.transcript_texts() != []
    finally:
        application.shutdown()
        application.window.deleteLater()


def make_jpeg(width: int = 64, height: int = 48) -> bytes:
    image = QImage(width, height, QImage.Format.Format_RGB32)
    image.fill(QColor("#336699"))
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    assert image.save(buffer, "JPG")
    return bytes(buffer.data())


def test_live_picture_is_shown_on_home_and_chat_pages(qt_app, vision_app):
    application, camera = vision_app
    application.window.show()
    screen = application.window.user_screen
    screen.show_page(UserScreen.PAGE_HOME)  # kept, though no longer shown by itself
    pump(qt_app, 0.2)
    assert application.vision.frame.isNull()
    # No picture: no empty camera box anywhere on the home page.
    assert not screen._home_camera.isVisible() and not screen._home_camera_small.isVisible()
    assert screen.showcase.isVisible()  # the Admin's default: announcements/media fill the home page

    camera.set_frame(make_jpeg())
    camera.place_person(5.0)  # visible, but outside the service area
    wait_until(qt_app, lambda: not application.vision.frame.isNull())
    wait_until(qt_app, lambda: screen._home_camera_small.isVisible())
    assert screen.showcase.isVisible() and not screen._home_camera.isVisible()
    screen._home_camera_small.grab()

    application.content.set_home_display(HOME_CAMERA)  # Admin prefers the camera as the main view
    assert screen._home_camera.isVisible()
    assert not screen.showcase.isVisible() and not screen._home_camera_small.isVisible()
    assert (application.vision.frame.width(), application.vision.frame.height()) == (64, 48)
    # Tells the passer-by they have been seen (after a few frames of smoothing).
    wait_until(qt_app, lambda: "5.0" in screen._home_camera._caption)
    (person,) = application.vision.people
    assert person.box is not None and not application.vision.is_at_conversation_distance(person)
    screen._home_camera.grab()  # painting with a picture and a box must not raise

    camera.place_person(1.0)
    wait_until(qt_app, lambda: application.vision.present)
    assert application.vision.is_at_conversation_distance(application.vision.people[0])
    screen.show_page(UserScreen.PAGE_CHAT)
    assert screen._chat_camera.isVisible()  # on the chat page too
    screen._chat_camera.grab()

    camera.set_frame(None)  # picture lost (preview off / camera gone)
    wait_until(qt_app, lambda: application.vision.frame.isNull())
    wait_until(qt_app, lambda: not screen._chat_camera.isVisible())


def test_corrupt_picture_data_is_ignored(qt_app, vision_app):
    application, camera = vision_app
    camera.set_frame(b"this is not a jpeg")
    pump(qt_app, 0.3)
    assert application.vision.frame.isNull()
    assert not application.window.user_screen._home_camera_small.isVisible()


def test_app_runs_without_a_camera(qt_app, settings):
    application = Application(settings, QtLogHandler())  # VISION_ENABLED=false
    try:
        assert application.vision.status is SourceStatus.DISABLED
        assert not application.vision.present
        pump(qt_app, 0.3)
        assert application.window.user_screen.current_page() == UserScreen.PAGE_CHAT
    finally:
        application.shutdown()
        application.window.deleteLater()


def test_home_page_falls_back_when_the_chosen_content_is_missing(qt_app, vision_app):
    application, camera = vision_app
    application.window.show()
    screen, content = application.window.user_screen, application.content
    screen.show_page(UserScreen.PAGE_HOME)  # kept, though no longer shown by itself
    camera.set_frame(make_jpeg())
    wait_until(qt_app, lambda: not application.vision.frame.isNull())

    for announcement in content.announcements():  # nothing left for a "media" home page
        content.delete_announcement(announcement.id)
    assert content.home_display() == HOME_MEDIA
    assert screen._home_camera.isVisible() and not screen.showcase.isVisible()

    camera.set_frame(None)  # ...and no camera either: the invitation stands alone
    wait_until(qt_app, lambda: application.vision.frame.isNull())
    wait_until(qt_app, lambda: not screen._home_main.isVisible())
    assert screen._welcome_label.isVisible()

    with pytest.raises(ContentError):
        content.set_home_display("hologram")


def test_media_stops_while_talking_and_resumes_on_home(qt_app, vision_app):
    application, camera = vision_app
    application.window.show()
    screen = application.window.user_screen
    assert not screen.showcase._active  # opens on the conversation: nothing plays
    screen.show_page(UserScreen.PAGE_HOME)  # kept, though no longer shown by itself
    assert screen.showcase._active  # announcements rotating on the home page
    camera.place_person(1.0)
    wait_until(qt_app, lambda: application.vision.present)
    assert screen.current_page() == UserScreen.PAGE_HOME and screen.showcase._active  # greeted in place
    screen.show_page(UserScreen.PAGE_CHAT)
    assert not screen.showcase._active  # conversation page: no sound over the microphone
    camera.clear()
    wait_until(qt_app, lambda: not application.vision.present)
    assert screen.current_page() == UserScreen.PAGE_CHAT  # left: cleared, but the page stays
    screen.show_page(UserScreen.PAGE_HOME)
    assert screen.showcase._active

    application.window.enter_admin()
    assert not screen.showcase._active  # hidden behind Admin
    application.window.leave_admin()
    assert screen.showcase._active


def test_listening_goes_on_after_an_answer_even_when_the_camera_sees_nobody(qt_app, tmp_path):
    camera, microphone = MockVisionSource(), FakeMicrophone()
    application = Application(
        make_settings(tmp_path, STT_ENABLED="true", VISION_LEAVE_SECONDS="30"),
        QtLogHandler(), microphone=microphone, stt=FakeStt(), vision_source=camera,
    )
    try:
        wait_until(qt_app, lambda: application.voice.status.value == "ready")
        pump(qt_app, 0.3)  # camera running, nobody in front
        application.window._listen_timer.setInterval(100)  # the suite keeps it off (conftest); this test wants it on
        application.window._listen_timer.start()
        finished = []
        application.conversation.turn_finished.connect(lambda answered: finished.append(answered))
        application.window.user_screen.show_page(UserScreen.PAGE_CHAT)
        application.conversation.ask(application.settings.faq[0].question)
        wait_until(qt_app, lambda: finished, timeout_s=12.0)
        wait_until(qt_app, lambda: microphone.recording, timeout_s=6.0)  # sound that comes in is heard and answered
    finally:
        application.shutdown()
        application.window.deleteLater()


def test_smart_welcome_greets_in_the_service_area_and_listens_all_the_time(qt_app, tmp_path):
    camera, microphone = MockVisionSource(), FakeMicrophone()
    application = Application(
        make_settings(tmp_path, STT_ENABLED="true", MIC_NO_SPEECH_TIMEOUT_S="30"),
        QtLogHandler(), microphone=microphone, stt=FakeStt(), vision_source=camera,
    )
    try:
        wait_until(qt_app, lambda: application.voice.status.value == "ready")
        screen = application.window.user_screen
        greeting = greeting_now(application)

        screen.show_page(UserScreen.PAGE_CHAT)  # hands-free listening works on the chat page
        camera.place_person(5.0)  # walking by, far away: nothing happens
        pump(qt_app, 0.8)
        assert not application.vision.present
        assert screen.transcript_texts() == []

        camera.place_person(2.5)  # has come into the service area
        wait_until(qt_app, lambda: application.vision.present)
        pump(qt_app, 0.5)
        assert screen.current_page() == UserScreen.PAGE_CHAT
        assert screen.transcript_texts() == [f"หุ่นยนต์: {greeting}"]
        assert not application.vision.engaged
        assert application.conversation.state.state is ConversationState.SPEAKING  # saying the greeting
        # (9 Nov: greeted, and then listening at once, even from the edge of the service area: not only up close)
        wait_until(qt_app, lambda: microphone.recording, timeout_s=8.0)
        assert application.conversation.state.state is ConversationState.LISTENING

        camera.place_person(1.0)  # steps up to the robot to talk
        wait_until(qt_app, lambda: application.vision.engaged)
        assert microphone.recording  # (it was open already: stepping up does not interrupt or restart it)
        assert application.conversation.state.state is ConversationState.LISTENING
        assert screen.transcript_texts() == [f"หุ่นยนต์: {greeting}"]  # greeted once, not twice

        camera.clear()
        wait_until(qt_app, lambda: not application.vision.present)
        pump(qt_app, 0.6)
        assert microphone.recording  # the camera losing them does not end the conversation
        assert screen.current_page() == UserScreen.PAGE_CHAT and screen.transcript_texts() == [f"หุ่นยนต์: {greeting}"]

        actions = [e.action for e in application.content.recent_activity() if e.actor == "vision"]
        assert actions == ["visitor.left", "visitor.engaged", "visitor.arrived"]  # newest first
    finally:
        application.shutdown()
        application.window.deleteLater()


def test_walking_straight_up_to_the_robot_greets_and_listens(qt_app, tmp_path):
    camera, microphone = MockVisionSource(), FakeMicrophone()
    application = Application(
        # The "stayed in the area" test is slow here, so conversation distance is reached first.
        make_settings(tmp_path, STT_ENABLED="true", MIC_NO_SPEECH_TIMEOUT_S="30",
                      VISION_GREET_DWELL_SECONDS="5", VISION_GREET_STILL_SECONDS="0"),
        QtLogHandler(), microphone=microphone, stt=FakeStt(), vision_source=camera,
    )
    try:
        wait_until(qt_app, lambda: application.voice.status.value == "ready")
        screen = application.window.user_screen
        screen.show_page(UserScreen.PAGE_CHAT)
        camera.place_person(0.8)
        wait_until(qt_app, lambda: microphone.recording, timeout_s=8.0)
        assert screen.transcript_texts() == [f"หุ่นยนต์: {greeting_now(application)}"]

        camera.clear()  # and leaves again before the slow test ever passed
        wait_until(qt_app, lambda: not application.vision.present, timeout_s=6.0)
        pump(qt_app, 0.6)
        assert microphone.recording and screen.transcript_texts() == [f"หุ่นยนต์: {greeting_now(application)}"]
    finally:
        application.shutdown()
        application.window.deleteLater()


def test_stepping_up_does_not_interrupt_a_visitor_using_the_touch_screen(qt_app, tmp_path):
    camera, microphone = MockVisionSource(), FakeMicrophone()
    application = Application(
        make_settings(tmp_path, STT_ENABLED="true", VOICE_FOLLOW_UP="false"),
        QtLogHandler(), microphone=microphone, stt=FakeStt(), vision_source=camera,
    )
    try:
        wait_until(qt_app, lambda: application.voice.status.value == "ready")
        camera.place_person(2.5)
        wait_until(qt_app, lambda: application.vision.present)
        application.conversation.ask(application.settings.faq[0].question)  # taps a menu button
        camera.place_person(1.0)  # ...and leans in while the robot is answering
        wait_until(qt_app, lambda: application.vision.engaged)
        pump(qt_app, 0.3)
        assert not microphone.recording
        assert application.conversation.state.state in (
            ConversationState.THINKING, ConversationState.SPEAKING,
        )
    finally:
        application.shutdown()
        application.window.deleteLater()


# --- the robot greets only a visitor who stops in front of the camera ---


def test_without_a_camera_the_robot_says_nothing_at_start_or_after_the_idle_reset(qt_app, tmp_path):
    camera = MockVisionSource()
    application = Application(make_settings(tmp_path), QtLogHandler(), vision_source=camera)
    try:
        camera.stop()  # status DISABLED: nothing will ever walk into view
        screen = application.window.user_screen
        application.window.watch_camera_for_greeting()  # what Application.show() does
        pump(qt_app, 0.3)
        assert screen.transcript_texts() == []  # no greeting at start-up, to nobody

        application.window._on_idle()
        assert screen.transcript_texts() == []
    finally:
        application.shutdown()
        application.window.deleteLater()


def test_a_working_camera_greets_nobody_until_someone_comes(qt_app, vision_app):
    application, camera = vision_app
    screen = application.window.user_screen
    application.window.watch_camera_for_greeting()
    wait_until(qt_app, lambda: application.vision.status.value == "running")
    pump(qt_app, 0.5)
    assert screen.transcript_texts() == []


def still_settings(tmp_path, still="1.0", **extra):
    return make_settings(tmp_path, VISION_GREET_STILL_SECONDS=still, **extra)


def test_someone_who_stops_in_front_of_the_camera_is_greeted_and_a_passer_by_is_not(qt_app, tmp_path):
    camera = MockVisionSource()
    application = Application(still_settings(tmp_path), QtLogHandler(), vision_source=camera)
    try:
        screen = application.window.user_screen
        wait_until(qt_app, lambda: application.vision.status.value == "running")
        # Walks across the picture, a metre in a second, close to the camera: in range, but never still.
        deadline = time.monotonic() + 2.5
        x = -0.9
        while time.monotonic() < deadline:
            camera.place_person(1.5, x_m=x)
            x += 0.04
            if x > 0.9:
                x = -0.9
            pump(qt_app, 0.05)
        assert screen.transcript_texts() == []  # walking past: not greeted
        camera.clear()
        wait_until(qt_app, lambda: not application.vision.present, timeout_s=6.0)
        application.conversation.stop()  # the farewell to the passer-by (who came within talking distance) is over
        wait_until(qt_app, lambda: application.conversation.state.state is ConversationState.IDLE)
        screen.clear_session()
        application.window._last_greeting = float("-inf")

        camera.place_person(1.5, x_m=0.1)  # and now someone stops and looks at it
        wait_until(qt_app, lambda: screen.transcript_texts() == [f"หุ่นยนต์: {greeting_now(application)}"], timeout_s=6.0)
    finally:
        application.shutdown()
        application.window.deleteLater()


def test_a_visitor_is_greeted_once_per_visit_however_long_they_stand(qt_app, tmp_path):
    camera = MockVisionSource()
    application = Application(still_settings(tmp_path, "0.5"), QtLogHandler(), vision_source=camera)
    attentive = []
    application.vision.visitor_attentive.connect(attentive.append)
    try:
        wait_until(qt_app, lambda: application.vision.status.value == "running")
        camera.place_person(1.5)
        wait_until(qt_app, lambda: attentive, timeout_s=5.0)
        pump(qt_app, 1.5)
        assert len(attentive) == 1
    finally:
        application.shutdown()
        application.window.deleteLater()


def test_by_default_anyone_who_comes_into_range_is_greeted_once_even_if_they_keep_moving(qt_app, tmp_path):
    """Changed 9 Nov 2026: no standing still is needed (VISION_GREET_STILL_SECONDS defaults to 0)."""
    camera = MockVisionSource()
    application = Application(make_settings(tmp_path), QtLogHandler(), vision_source=camera)
    try:
        assert application.settings.vision_greet_still_s == 0.0
        screen = application.window.user_screen
        wait_until(qt_app, lambda: application.vision.status.value == "running")
        x = -0.6
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:  # walking about in front of the camera, never still
            camera.place_person(1.5, x_m=x)
            x = x + 0.05 if x < 0.6 else -0.6
            pump(qt_app, 0.05)
        wait_until(qt_app, lambda: screen.transcript_texts() == [f"หุ่นยนต์: {greeting_now(application)}"], timeout_s=6.0)
        pump(qt_app, 1.0)
        assert len(screen.transcript_texts()) == 1  # once: not again while they stay
    finally:
        application.shutdown()
        application.window.deleteLater()


def test_a_robot_that_is_only_sitting_listening_still_greets_a_visitor_who_arrives(qt_app, tmp_path):
    """Always listening (9 Nov) must not mean "a conversation is already going on": nobody has spoken yet."""
    camera, microphone = MockVisionSource(), FakeMicrophone()
    application = Application(
        make_settings(tmp_path, STT_ENABLED="true", MIC_NO_SPEECH_TIMEOUT_S="30"),
        QtLogHandler(), microphone=microphone, stt=FakeStt(), vision_source=camera,
    )
    try:
        wait_until(qt_app, lambda: application.voice.status.value == "ready")
        screen = application.window.user_screen
        screen.show_page(UserScreen.PAGE_CHAT)
        screen.keep_listening()  # what the always-on timer does in the real app
        wait_until(qt_app, lambda: microphone.recording)
        assert application.conversation.state.state is ConversationState.LISTENING and screen.transcript_texts() == []

        camera.place_person(2.0)
        wait_until(qt_app, lambda: screen.transcript_texts() == [f"หุ่นยนต์: {greeting_now(application)}"], timeout_s=8.0)
        wait_until(qt_app, lambda: microphone.recording, timeout_s=8.0)  # and listening again after the greeting
    finally:
        application.shutdown()
        application.window.deleteLater()


def test_a_visitor_who_is_being_heard_is_not_greeted_over(qt_app, tmp_path):
    camera, microphone = MockVisionSource(), FakeMicrophone()
    application = Application(
        make_settings(tmp_path, STT_ENABLED="true", MIC_NO_SPEECH_TIMEOUT_S="30"),
        QtLogHandler(), microphone=microphone, stt=FakeStt(), vision_source=camera,
    )
    try:
        wait_until(qt_app, lambda: application.voice.status.value == "ready")
        screen = application.window.user_screen
        screen.show_page(UserScreen.PAGE_CHAT)
        application.voice.visitor_gate = None  # (this test is about the greeting, not about who is in front of the camera)
        screen.keep_listening()
        wait_until(qt_app, lambda: microphone.recording)
        microphone.level = 0.3  # someone is talking
        wait_until(qt_app, lambda: application.voice.heard_speech, timeout_s=4.0)
        camera.place_person(2.0)
        wait_until(qt_app, lambda: application.vision.present)
        pump(qt_app, 1.0)
        assert screen.transcript_texts() == []  # not greeted: they are mid-sentence
        assert application.conversation.state.state is ConversationState.LISTENING
    finally:
        application.shutdown()
        application.window.deleteLater()
