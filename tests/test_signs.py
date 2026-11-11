"""Cards and gestures: the tracker, the detector on drawn pictures, card images, and the app's reactions."""
from __future__ import annotations

import time

import pytest
from PySide6.QtCore import QBuffer, QIODevice
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import QApplication

from app.application import Application
from app.content_controller import ContentError
from app.state import ConversationState
from config.settings import load_settings
from database.db import SCHEMA_VERSION, Database
from database.models import (
    ACTION_FEEDBACK_UP,
    ACTION_SAY,
    ACTION_STOP,
    TRIGGER_CARD,
    TRIGGER_GESTURE,
)
from ui.card_image import render_card
from ui.fonts import BUNDLED_FAMILY, apply_app_font
from ui.log_bridge import QtLogHandler
from ui.user_screen import UserScreen
from vision.aruco_4x4_50 import MARKER_COUNT, PATTERNS, cells
from vision.mock_source import MockVisionSource
from ui.robot_face import MOOD_CALM, MOOD_LOVE, MOOD_SAD, MOOD_STARSTRUCK, MOODS
from ui.user_screen import FACE_CAPTIONS, GESTURE_MOODS
from vision.signs import (
    GESTURE_LABELS,
    KIND_CARD,
    KIND_GESTURE,
    Sign,
    Hand,
    SignDetector,
    SignTracker,
    SignTrackerConfig,
    choose_gesture,
)

CARD_7 = Sign(KIND_CARD, "7")
THUMB_UP = Sign(KIND_GESTURE, "Thumb_Up")
STEP = 0.15


class Showing:
    """Feeds a tracker one picture every 150 ms, as the camera process does."""

    def __init__(self) -> None:
        self.tracker = SignTracker(SignTrackerConfig())
        self.now = 50.0
        self.events: list[Sign] = []

    def show(self, seconds: float, *signs: Sign) -> list[Sign]:
        new = []
        for _ in range(round(seconds / STEP)):
            self.now += STEP
            new += self.tracker.update(list(signs), self.now)
        self.events += new
        return new


# --- tracker ---


def test_card_held_up_is_reported_once_however_long_it_is_held():
    showing = Showing()
    showing.show(5.0, CARD_7)
    assert showing.events == [CARD_7]


def test_card_shown_again_after_being_put_down_counts_again():
    showing = Showing()
    showing.show(1.0, CARD_7)
    showing.show(0.6)  # lowered briefly: still the same showing
    showing.show(1.0, CARD_7)
    assert showing.events == [CARD_7]
    showing.show(2.0)  # put down
    showing.show(1.0, CARD_7)
    assert showing.events == [CARD_7, CARD_7]


def test_a_glimpse_is_not_enough_and_gestures_need_longer_than_cards():
    showing = Showing()
    showing.show(0.15, CARD_7)
    showing.show(1.0)
    showing.show(0.45, THUMB_UP)  # a hand passing through the shape
    showing.show(1.0)
    assert showing.events == []
    showing.show(0.6, CARD_7)  # the same short time is enough for a card
    assert showing.events == [CARD_7]
    showing.show(0.9, THUMB_UP)
    assert showing.events == [CARD_7, THUMB_UP]


def test_detector_missing_one_picture_does_not_restart_or_repeat_the_showing():
    showing = Showing()
    for _ in range(6):
        showing.show(0.15, THUMB_UP)
        showing.show(0.15)  # every other picture misses the hand
    assert showing.events == [THUMB_UP]


def test_two_signs_at_once_are_tracked_separately():
    showing = Showing()
    showing.show(1.5, CARD_7, THUMB_UP)
    assert sorted(showing.events, key=lambda s: s.kind) == [CARD_7, THUMB_UP]


THUMB_DOWN = Sign(KIND_GESTURE, "Thumb_Down")
LOVE = Sign(KIND_GESTURE, "ILoveYou")


def test_shapes_the_hand_passes_through_after_a_gesture_do_not_count():
    """The log of 5 Nov: "thumb down", then "I love you" 0.24 s later, from one hand turning."""
    showing = Showing()
    showing.show(0.9, THUMB_DOWN)
    showing.show(0.9, LOVE)  # the hand turning away, read as another sign for a moment
    assert showing.events == [THUMB_DOWN]
    showing.show(1.5, LOVE)  # held on purpose: it counts
    assert showing.events == [THUMB_DOWN, LOVE]


def test_a_gesture_flickering_with_another_counts_only_if_it_is_most_of_what_was_seen():
    showing = Showing()
    for _ in range(4):  # mostly thumb up, now and then misread
        showing.show(0.3, THUMB_UP)
        showing.show(0.15, LOVE)
    assert showing.events == [THUMB_UP]

    showing = Showing()
    for _ in range(6):  # the model cannot decide: half and half
        showing.show(0.15, THUMB_UP)
        showing.show(0.15, LOVE)
    assert showing.events == []


# --- the hand's shape (MediaPipe's 21 landmarks, in picture pixels; y grows downwards) ---


def fist_with_thumb(up: bool) -> list[tuple[float, float]]:
    """A fist held sideways, the thumb sticking straight up (or down)."""
    hand = [
        (300, 300),  # wrist
        (320, 270), (335, 240), (340, 215), (343, 190),  # thumb
        (360, 260), (390, 262), (380, 272), (365, 275),  # index: curled back towards the palm
        (365, 280), (395, 282), (385, 290), (370, 292),
        (362, 300), (392, 302), (382, 306), (367, 308),
        (355, 318), (385, 318), (375, 321), (360, 322),
    ]
    return hand if up else [(x, 600 - y) for x, y in hand]


def love_hand(middle_straight: bool = False) -> list[tuple[float, float]]:
    """Index and little finger up, middle and ring curled, thumb out."""
    middle = [(305, 320), (305, 280), (305, 250), (305, 220)] if middle_straight else \
             [(305, 320), (305, 285), (305, 310), (305, 335)]
    return [
        (300, 400),
        (270, 380), (250, 360), (235, 345), (225, 330),
        (285, 320), (283, 280), (282, 250), (280, 225),
        *middle,
        (320, 322), (320, 290), (320, 312), (320, 338),
        (335, 330), (340, 295), (343, 270), (345, 245),
    ]


def test_the_thumbs_follow_the_hand_not_the_model_label():
    up, down = fist_with_thumb(True), fist_with_thumb(False)
    assert choose_gesture("Thumb_Up", 0.9, up) == "Thumb_Up"
    assert choose_gesture("Thumb_Up", 0.5, down) == "Thumb_Down"  # the model had the thumb the wrong way
    assert choose_gesture("None", 0.3, down) == "Thumb_Down"  # the model was unsure: the shape is clear
    assert choose_gesture("Closed_Fist", 0.9, up) == "Closed_Fist"  # a sure fist stays a fist


def test_a_thumb_label_the_shape_cannot_settle_is_kept_when_the_model_is_sure():
    sideways = [(x, y) for x, y in fist_with_thumb(True)]
    sideways[4] = (400, 245)  # thumb tip pointing to the side
    sideways[3] = (370, 242)
    assert choose_gesture("Thumb_Up", 0.8, sideways) == "Thumb_Up"
    assert choose_gesture("Thumb_Up", 0.5, sideways) is None
    assert choose_gesture("Thumb_Up", 0.8, []) == "Thumb_Up"  # no landmarks: the label as before


def test_i_love_you_is_counted_only_with_the_right_fingers():
    assert choose_gesture("ILoveYou", 0.8, love_hand()) == "ILoveYou"
    assert choose_gesture("ILoveYou", 0.8, love_hand(middle_straight=True)) is None
    assert choose_gesture("ILoveYou", 0.8, fist_with_thumb(False)) == "Thumb_Down"  # the 5 Nov mix-up
    assert choose_gesture("Victory", 0.4, love_hand()) is None  # unsure, and not a thumb


def test_every_gesture_has_a_face_and_every_face_a_caption():
    assert set(GESTURE_MOODS) == set(GESTURE_LABELS)
    shared = {"Wave_Visitor_Right", "Wave_Visitor_Left"}  # the two goodbyes share one
    assert len({mood for code, mood in GESTURE_MOODS.items() if code not in shared}) == len(GESTURE_MOODS) - len(shared)  # each its own face
    assert set(FACE_CAPTIONS) == set(MOODS)


# --- marker data, card image, detector (real OpenCV, drawn pictures) ---


def to_jpeg(image: QImage) -> bytes:
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    assert image.save(buffer, "JPG", 85)
    return bytes(buffer.data())


def test_marker_patterns_match_the_dictionary_opencv_detects_with():
    import cv2

    dictionary = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
    assert len(PATTERNS) == MARKER_COUNT == 50 and len(set(PATTERNS)) == 50
    for marker_id in (0, 7, 23, 49):
        reference = cv2.aruco.generateImageMarker(dictionary, marker_id, 6)[1:5, 1:5] > 127
        assert cells(marker_id) == reference.tolist()


@pytest.fixture(scope="module")
def qt_app():
    qt_app = QApplication.instance() or QApplication([])
    apply_app_font(qt_app)
    return qt_app


@pytest.mark.parametrize("marker_id", [0, 7, 49])
def test_printed_card_is_read_back_by_the_detector_at_camera_size(qt_app, tmp_path, marker_id):
    card = render_card(marker_id, "แผนกเทคโนโลยีสารสนเทศ", BUNDLED_FAMILY)
    assert card.width() < card.height()  # portrait, like the A6 it is printed on

    # The card as the camera would see it: small, inside a 640x480 picture of a room.
    frame = QImage(640, 480, QImage.Format.Format_RGB32)
    frame.fill(QColor("#6b7280"))
    painter = QPainter(frame)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    painter.drawImage(frame.rect().adjusted(260, 150, -260, -160), card)  # 120 x 170 px
    painter.end()

    detector = SignDetector(str(tmp_path), gestures=False)
    assert detector.detect(to_jpeg(frame)) == [(KIND_CARD, str(marker_id))]


def test_detector_finds_nothing_in_an_empty_room_or_in_garbage(qt_app, tmp_path):
    detector = SignDetector(str(tmp_path), gestures=False)
    room = QImage(640, 480, QImage.Format.Format_RGB32)
    room.fill(QColor("#6b7280"))
    assert detector.detect(to_jpeg(room)) == []
    assert detector.detect(b"not a jpeg") == []
    assert not detector.gestures_ready


# --- database ---


def test_version_1_database_is_upgraded_and_keeps_its_data(tmp_path):
    import sqlite3

    from database import db as db_module

    path = tmp_path / "old.db"
    connection = sqlite3.connect(path)
    connection.executescript(db_module._SCHEMA)  # what V0.2–V0.4 created
    connection.execute("PRAGMA user_version = 1")
    connection.execute(
        "INSERT INTO announcements (title, created_at, updated_at) VALUES ('ประกาศเดิม', 'a', 'b')"
    )
    connection.commit()
    connection.close()

    database = Database(path)
    database.initialize()
    with database.connect() as upgraded:
        assert upgraded.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        assert upgraded.execute("SELECT title FROM announcements").fetchone()[0] == "ประกาศเดิม"
        assert upgraded.execute("SELECT COUNT(*) FROM visual_triggers").fetchone()[0] == 0
    database.initialize()  # starting again changes nothing


# --- reactions in the application ---


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


@pytest.fixture
def sign_app(qt_app, tmp_path):
    settings = load_settings(
        environ={
            "DATA_DIR": str(tmp_path / "data"), "LOG_DIR": str(tmp_path / "logs"),
            "MEDIA_DIR": str(tmp_path / "media"), "STT_ENABLED": "false", "TTS_ENABLED": "false",
            "VISION_DWELL_SECONDS": "0.2", "VISION_GREET_DWELL_SECONDS": "0.3", "VISION_GREET_STILL_SECONDS": "0",
            "VISION_LEAVE_SECONDS": "30",
        }
    )
    camera = MockVisionSource()
    application = Application(settings, QtLogHandler(), vision_source=camera)
    yield application, camera
    application.shutdown()
    application.window.deleteLater()


def state(application) -> ConversationState:
    return application.conversation.state.state


def activity(application, actor: str) -> list[tuple[str, str]]:
    return [(e.action, e.detail) for e in application.content.recent_activity() if e.actor == actor]


def test_default_gestures_are_set_up_and_admin_input_is_validated(sign_app):
    application, _camera = sign_app
    content = application.content
    defaults = {(t.kind, t.code): t.action for t in content.triggers()}
    assert defaults[(TRIGGER_GESTURE, "Thumb_Up")] == ACTION_FEEDBACK_UP
    assert defaults[(TRIGGER_GESTURE, "Open_Palm")] == ACTION_STOP

    content.save_trigger(None, TRIGGER_CARD, "07", " แผนกไอที ", ACTION_STOP, "ยินดีต้อนรับสู่แผนกไอที", None, True)
    card = content.find_trigger(TRIGGER_CARD, "7")
    assert (card.title, card.action) == ("แผนกไอที", ACTION_SAY)  # number normalised; a card always "says"

    for bad in (
        (TRIGGER_CARD, "50", "x", ACTION_SAY, "ข้อความ"),  # no such card
        (TRIGGER_CARD, "abc", "x", ACTION_SAY, "ข้อความ"),
        (TRIGGER_CARD, "7", "ซ้ำ", ACTION_SAY, "ข้อความ"),  # already used
        (TRIGGER_CARD, "8", "", ACTION_SAY, "ข้อความ"),  # no title
        (TRIGGER_CARD, "8", "ว่าง", ACTION_SAY, ""),  # nothing to say or show
        (TRIGGER_GESTURE, "Moonwalk", "x", ACTION_SAY, "ข้อความ"),
        (TRIGGER_GESTURE, "Victory", "x", "launch", "ข้อความ"),
        ("smell", "1", "x", ACTION_SAY, "ข้อความ"),
    ):
        with pytest.raises(ContentError):
            content.save_trigger(None, bad[0], bad[1], bad[2], bad[3], bad[4], None, True)

    content.save_trigger(card.id, TRIGGER_CARD, "7", "แผนกไอที", ACTION_SAY, "ข้อความใหม่", None, False)
    assert content.find_trigger(TRIGGER_CARD, "7") is None  # disabled
    content.delete_trigger(card.id)
    assert (TRIGGER_CARD, "7") not in {(t.kind, t.code) for t in content.triggers()}


def test_showing_a_card_makes_the_robot_present_its_information(qt_app, sign_app):
    application, camera = sign_app
    application.content.save_trigger(
        None, TRIGGER_CARD, "7", "แผนกไอที", ACTION_SAY, "แผนกไอทีสอนเขียนโปรแกรมและเครือข่ายครับ", None, True
    )
    screen = application.window.user_screen

    camera.show_signs(CARD_7)
    wait_until(qt_app, lambda: len(screen.transcript_texts()) == 2)
    assert screen.current_page() == UserScreen.PAGE_CHAT  # the only page shown; the answer is in the transcript
    assert screen.transcript_texts() == [
        "คุณแสดง: แผนกไอที", "หุ่นยนต์: แผนกไอทีสอนเขียนโปรแกรมและเครือข่ายครับ",
    ]
    assert state(application) is ConversationState.SPEAKING
    pump(qt_app, 1.0)
    assert len(screen.transcript_texts()) == 2  # still holding the card: not repeated
    assert ("sign.shown", "card 7 แผนกไอที") in activity(application, "vision")


def test_unassigned_card_and_a_gesture_from_a_passer_by_do_nothing(qt_app, sign_app):
    application, camera = sign_app
    screen = application.window.user_screen
    camera.show_signs(Sign(KIND_CARD, "33"), Sign(KIND_GESTURE, "Open_Palm"))  # nobody in the service area
    pump(qt_app, 1.2)
    assert screen.transcript_texts() == [] and screen.current_page() == UserScreen.PAGE_CHAT
    assert activity(application, "vision") == []


def test_thumbs_up_rates_the_last_answer_once(qt_app, sign_app):
    application, camera = sign_app
    screen = application.window.user_screen
    camera.place_person(1.0)
    wait_until(qt_app, lambda: application.vision.present)

    camera.show_signs(THUMB_UP)  # before any answer: nothing to rate
    pump(qt_app, 1.2)
    assert not any(action.startswith("feedback") for action, _ in activity(application, "visitor"))
    camera.show_signs()
    pump(qt_app, 1.8)

    application.conversation.ask(application.settings.faq[0].question)
    wait_until(qt_app, lambda: screen._feedback_row.isVisibleTo(screen))
    camera.show_signs(THUMB_UP)
    wait_until(qt_app, lambda: ("feedback.up", "source=knowledge language=th") in activity(application, "visitor"))
    assert not screen._feedback_row.isVisibleTo(screen)
    assert state(application) is ConversationState.SPEAKING  # saying thank you

    camera.show_signs()
    pump(qt_app, 1.8)
    camera.show_signs(THUMB_UP)  # same answer again: not counted twice
    pump(qt_app, 1.2)
    ratings = [a for a, _ in activity(application, "visitor") if a.startswith("feedback")]
    assert ratings == ["feedback.up"]


def test_feedback_buttons_on_screen_record_the_same_way(qt_app, sign_app):
    application, _camera = sign_app
    screen = application.window.user_screen
    application.conversation.ask("คำถามที่ไม่มีคำตอบแน่นอน")
    wait_until(qt_app, lambda: screen._feedback_row.isVisibleTo(screen))
    screen.feedback_given.emit(False)
    assert ("feedback.down", "source=fallback language=th") in activity(application, "visitor")
    assert not screen._feedback_row.isVisibleTo(screen)
    screen.feedback_given.emit(False)  # nothing left to rate
    assert len([a for a, _ in activity(application, "visitor") if a.startswith("feedback")]) == 1

    application.conversation.ask(application.settings.faq[0].question)
    wait_until(qt_app, lambda: screen._feedback_row.isVisibleTo(screen))
    screen.reset_session()  # the visitor leaves without rating
    assert not screen._feedback_row.isVisibleTo(screen)


def test_open_palm_stops_the_robot(qt_app, sign_app):
    application, camera = sign_app
    camera.place_person(1.0)
    wait_until(qt_app, lambda: application.vision.present)
    application.conversation.ask(application.settings.faq[0].question)
    wait_until(qt_app, lambda: state(application) is ConversationState.SPEAKING)
    camera.show_signs(Sign(KIND_GESTURE, "Open_Palm"))
    wait_until(qt_app, lambda: state(application) is ConversationState.IDLE)
    assert application.window.user_screen._message_bar.text() == "หยุดแล้วครับ"


def test_every_gesture_has_a_meaning_from_the_start(sign_app):
    application, _camera = sign_app
    gestures = {t.code: t for t in application.content.triggers() if t.kind == TRIGGER_GESTURE}
    assert set(gestures) == set(GESTURE_LABELS)
    assert gestures["ILoveYou"].action == ACTION_SAY and "รัก" in gestures["ILoveYou"].response_text


def test_gestures_get_their_face_and_a_spoken_reply(qt_app, sign_app):
    application, camera = sign_app
    screen = application.window.user_screen
    spoken = []
    application.conversation.reply_to_gesture = lambda text: spoken.append(text)
    camera.place_person(1.0)
    wait_until(qt_app, lambda: application.vision.present)

    camera.show_signs(LOVE)
    wait_until(qt_app, lambda: spoken)
    assert screen.face_mood() == MOOD_LOVE
    assert spoken == ["ขอบคุณครับ ผมก็รักทุกคนเลยครับ"]
    assert screen.transcript_texts()[-2:] == ["คุณแสดง: บอกรัก", "หุ่นยนต์: ขอบคุณครับ ผมก็รักทุกคนเลยครับ"]

    camera.show_signs()
    pump(qt_app, 1.8)
    camera.show_signs(THUMB_UP)  # nothing to rate: still thanked
    wait_until(qt_app, lambda: len(spoken) == 2)
    assert screen.face_mood() == MOOD_STARSTRUCK

    camera.show_signs()
    pump(qt_app, 1.8)
    camera.show_signs(THUMB_DOWN)
    wait_until(qt_app, lambda: len(spoken) == 3)
    assert screen.face_mood() == MOOD_SAD


def test_a_gesture_during_an_answer_shows_the_face_without_cutting_the_answer_off(qt_app, sign_app):
    application, camera = sign_app
    screen = application.window.user_screen
    spoken = []
    application.conversation.reply_to_gesture = lambda text: spoken.append(text)
    camera.place_person(1.0)
    wait_until(qt_app, lambda: application.vision.present)
    application.conversation.ask(application.settings.faq[0].question)
    wait_until(qt_app, lambda: state(application) is ConversationState.SPEAKING)
    camera.show_signs(THUMB_UP)
    wait_until(qt_app, lambda: screen.face_mood() == MOOD_STARSTRUCK)
    assert spoken == [] and state(application) is ConversationState.SPEAKING
    assert ("feedback.up", "source=knowledge language=th") in activity(application, "visitor")


def test_open_palm_closes_the_eyes_calmly(qt_app, sign_app):
    application, camera = sign_app
    camera.place_person(1.0)
    wait_until(qt_app, lambda: application.vision.present)
    camera.show_signs(Sign(KIND_GESTURE, "Open_Palm"))
    wait_until(qt_app, lambda: application.window.user_screen.face_mood() == MOOD_CALM)


def test_signs_do_not_reach_the_visitor_screen_while_admin_is_open(qt_app, sign_app):
    application, camera = sign_app
    application.content.save_trigger(None, TRIGGER_CARD, "7", "แผนกไอที", ACTION_SAY, "ข้อความ", None, True)
    application.window.enter_admin()
    camera.show_signs(CARD_7)
    pump(qt_app, 1.0)
    assert application.window.in_admin_mode()
    assert application.window.user_screen.transcript_texts() == []
    # The Admin's camera tab still shows what was seen, for checking a printed card.
    assert application.vision.last_sign == CARD_7


# --- the finger gun: index out sideways, thumb up (the sign that introduces the robot) ---


def finger_gun(direction: int = 1, curl: bool = True) -> list[tuple[float, float]]:
    """21 landmarks of the hand in the team's pictures; ``direction`` -1 mirrors it (the other hand)."""
    if curl:
        others = [(105, 80), (135, 95), (125, 105), (110, 110),  # middle
                  (95, 105), (120, 120), (112, 126), (100, 125),  # ring
                  (85, 125), (105, 135), (100, 140), (90, 140)]  # little
    else:  # the other three fingers straight out too: an open hand
        others = [(105, 80), (170, 85), (210, 85), (245, 85),
                  (95, 105), (160, 112), (200, 112), (235, 112),
                  (85, 125), (140, 135), (170, 135), (200, 135)]
    points = [(0, 100),
              (40, 80), (70, 60), (75, 20), (75, -20),  # thumb stands up
              (110, 50), (160, 45), (200, 45), (235, 45),  # index points sideways
              *others]
    return [(direction * x, y) for x, y in points]


def test_the_finger_gun_is_told_from_the_hands_shape_with_either_hand_and_either_direction():
    from vision.signs import is_finger_gun

    assert is_finger_gun(finger_gun(1)) and is_finger_gun(finger_gun(-1))
    # whatever the model called it, the shape decides
    assert choose_gesture("Pointing_Up", 0.9, finger_gun(1)) == "Finger_Gun"
    assert choose_gesture("None", 0.1, finger_gun(-1)) == "Finger_Gun"


def test_other_hands_are_not_the_finger_gun():
    from vision.signs import is_finger_gun

    assert not is_finger_gun(finger_gun(1, curl=False))  # an open hand
    assert not is_finger_gun(love_hand())  # index and little finger up
    assert not is_finger_gun(fist_with_thumb(True))  # a thumbs up
    assert not is_finger_gun(finger_gun(1)[:10])  # no full set of landmarks
    folded_thumb = finger_gun(1)
    folded_thumb[4] = (60, 70)  # the thumb lies along the hand: just a pointing finger
    assert not is_finger_gun(folded_thumb)


def test_the_l_sign_introduces_the_robot_with_a_face_that_lasts_as_long_as_the_speech(qt_app, sign_app):
    from ui.robot_face import MOOD_WELCOME

    application, camera = sign_app
    screen = application.window.user_screen
    spoken = []
    application.conversation.reply_to_gesture = lambda text: spoken.append(text)
    camera.place_person(1.0)
    wait_until(qt_app, lambda: application.vision.present)

    camera.show_signs(Sign(KIND_GESTURE, "Finger_Gun"))
    wait_until(qt_app, lambda: spoken)
    assert "คณะกรรมการ" in spoken[0] and "AIRA" in spoken[0]  # the introduction the team wrote
    assert screen.face_mood() == MOOD_WELCOME
    assert screen._mood_timer.remainingTime() > 30_000  # not the few seconds of the other gestures

    screen._on_state_changed(ConversationState.SPEAKING, ConversationState.SPEAKING)
    assert screen.face_mood() == MOOD_WELCOME  # still talking
    screen._on_state_changed(ConversationState.SPEAKING, ConversationState.IDLE)
    assert screen.face_mood() != MOOD_WELCOME  # the introduction is over: back to the robot's own state


def test_after_the_l_sign_introduction_the_conversation_is_paused_until_someone_new_comes_into_range(qt_app, sign_app):
    """9 Nov 2026: that gesture ends the conversation as "หยุดสนทนา" does; a person coming into range starts it again."""
    application, camera = sign_app
    screen = application.window.user_screen
    spoken = []
    application.conversation.reply_to_gesture = lambda text: spoken.append(text)
    camera.place_person(1.0)
    wait_until(qt_app, lambda: application.vision.present)

    camera.show_signs(Sign(KIND_GESTURE, "Finger_Gun"))
    wait_until(qt_app, lambda: spoken)
    assert not screen.listening_paused  # still introducing itself
    screen._on_state_changed(ConversationState.SPEAKING, ConversationState.IDLE)  # the introduction has been spoken
    assert screen.listening_paused and "หยุดสนทนาแล้ว" in screen._message_bar.text()
    assert not application.conversation.session_active

    # The presenter is still standing there: nothing restarts it (no new arrival).
    pump(qt_app, 1.0)
    assert screen.listening_paused

    camera.show_signs()
    from vision.presence import PresenceEvent, PresenceEventType

    application.window._on_visitor_arrived(PresenceEvent(PresenceEventType.ARRIVED, distance_m=2.0))  # someone new comes into range
    assert not screen.listening_paused
    assert "เริ่มสนทนาแล้ว" in screen._message_bar.text()


def test_a_pause_made_with_the_stop_button_is_not_ended_by_someone_arriving(qt_app, sign_app):
    application, camera = sign_app
    screen = application.window.user_screen
    screen._stop_conversation()
    assert screen.listening_paused
    camera.place_person(2.0)
    wait_until(qt_app, lambda: application.vision.present)
    pump(qt_app, 0.6)
    assert screen.listening_paused  # only "เริ่มสนทนา" ends the visitor's own pause
    screen._start_conversation()
    assert not screen.listening_paused


def test_someone_speaking_over_the_introduction_cancels_the_pause(qt_app, sign_app):
    application, camera = sign_app
    screen = application.window.user_screen
    application.conversation.reply_to_gesture = lambda text: None
    camera.place_person(1.0)
    wait_until(qt_app, lambda: application.vision.present)
    camera.show_signs(Sign(KIND_GESTURE, "Finger_Gun"))
    wait_until(qt_app, lambda: screen._pause_when_done)
    screen._on_state_changed(ConversationState.SPEAKING, ConversationState.LISTENING)  # a question came in
    screen._on_state_changed(ConversationState.SPEAKING, ConversationState.IDLE)
    assert not screen.listening_paused


def test_a_person_who_stays_in_front_of_the_camera_after_the_l_sign_restarts_the_conversation_by_looking(qt_app, sign_app, monkeypatch):
    """9 Nov, found on the real machine: the presenter stays where they are, so no arrival comes; the face being seen is enough."""
    import ui.main_window as main_window

    monkeypatch.setattr(main_window, "LOOKING_RESUME_S", 0.4)
    application, camera = sign_app
    screen, window = application.window.user_screen, application.window
    application.conversation.reply_to_gesture = lambda text: None
    camera.place_person(0.6)
    camera.set_mouth("still")  # a face is seen
    wait_until(qt_app, lambda: application.vision.present)
    camera.show_signs(Sign(KIND_GESTURE, "Finger_Gun"))
    wait_until(qt_app, lambda: screen._pause_when_done)
    screen._on_state_changed(ConversationState.SPEAKING, ConversationState.IDLE)
    assert screen.paused_by_gesture
    deadline = time.monotonic() + 6.0
    while screen.listening_paused and time.monotonic() < deadline:
        window._resume_when_looking()  # (the always-listening timer calls this; the suite keeps that timer off)
        qt_app.processEvents()
        time.sleep(0.05)
    assert not screen.listening_paused
    # ... and without a face (or anyone there) it stays paused
    screen._pause_listening("gesture")
    camera.clear()
    camera.set_mouth(None)
    pump(qt_app, 2.3)  # the face seen a moment ago is no longer "now"
    for _ in range(20):
        window._resume_when_looking()
        pump(qt_app, 0.05)
    assert screen.paused_by_gesture


# --- the "6 7" gesture: two hands swinging up and down in turn (10 Nov 2026) ---


def swing(detector, hand_gap=3.0, start=0.0, steps=14, step_s=0.15, amplitude=1.5, palm=60.0):
    """The left and right hand moving opposite ways, as pictures arrive every ``step_s`` seconds."""
    import math

    result = []
    for index in range(steps):
        now = start + index * step_s
        wave = math.sin(index * 0.9) * amplitude * palm  # about 0.4 s a swing at 6 pictures a second
        hands = [(200.0, 240.0 + wave, palm), (200.0 + hand_gap * palm, 240.0 - wave, palm)]
        result.append(detector.update(now, hands))
    return result


def test_two_hands_swinging_in_turn_are_the_67_gesture():
    from vision.signs import SixSevenDetector

    detector = SixSevenDetector()
    states = swing(detector)
    assert not states[0] and not states[1]  # not on the first picture
    assert any(states)  # after a few swings


def test_both_hands_rising_together_or_one_hand_waving_is_not_67():
    from vision.signs import SixSevenDetector

    together = SixSevenDetector()
    states = []
    for index in range(14):
        height = 240.0 + (60 if index % 4 < 2 else -60)
        states.append(together.update(index * 0.15, [(200.0, height, 60.0), (380.0, height, 60.0)]))
    assert not any(states)
    one = SixSevenDetector()
    states = [one.update(index * 0.15, [(200.0, 240.0 + (80 if index % 3 else -80), 60.0), (380.0, 240.0, 60.0)]) for index in range(14)]
    assert not any(states)
    assert not any(SixSevenDetector().update(index * 0.15, [(200.0, 240.0, 60.0)]) for index in range(14))  # one hand only


def test_hands_close_together_are_not_67_and_a_pause_ends_it():
    from vision.signs import SixSevenDetector

    assert not any(swing(SixSevenDetector(), hand_gap=0.5))  # hands nearly touching
    detector = SixSevenDetector()
    assert any(swing(detector))
    assert detector.update(10.0, []) is False  # the hands are gone


def test_the_67_gesture_has_a_label_and_a_trigger_that_says_six_seven(qt_app, sign_app):
    from vision.signs import GESTURE_LABELS

    assert "67" in GESTURE_LABELS["Six_Seven"]
    application, _camera = sign_app
    trigger = application.content.find_trigger("gesture", "Six_Seven")
    assert trigger is not None and trigger.response_text == "Six seven."


# --- a visitor waving goodbye (11 Nov 2026) ---


def waving(detector, centre_x=120.0, strokes_per_s=3.0, seconds=2.0, step_s=0.1, palm=60.0, amplitude=1.4, width=640.0, lift=0.0):
    import math

    hands_seen = []
    steps = int(seconds / step_s)
    for index in range(steps):
        now = index * step_s
        x = centre_x + amplitude * palm * math.sin(2 * math.pi * strokes_per_s / 2 * now)
        hands_seen.append(detector.update(now, [Hand(x, 240.0 + lift * index, palm, True, width)], centre=0.5))
    return hands_seen


def test_an_open_hand_swung_from_side_to_side_is_a_goodbye_after_about_three_waves():
    from vision.signs import WAVE_RIGHT_NAME, WaveDetector

    detector = WaveDetector()
    states = waving(detector)
    assert not states[0] and not states[3]  # not at the first swing
    assert any(states)
    first = states.index(True)
    assert first * 0.1 <= 1.7  # "at once": about when the third wave is made (five strokes at three a second)
    assert detector.side == WAVE_RIGHT_NAME  # on the left of the picture: the visitor's right hand


def test_the_side_of_the_picture_tells_which_hand_waved():
    from vision.signs import WAVE_LEFT_NAME, WaveDetector

    detector = WaveDetector()
    assert any(waving(detector, centre_x=500.0))
    assert detector.side == WAVE_LEFT_NAME


def test_a_hand_held_still_a_closed_hand_and_a_hand_going_up_and_down_are_not_goodbyes():
    from vision.signs import Hand, WaveDetector

    still = WaveDetector()
    assert not any(still.update(index * 0.1, [Hand(300.0, 240.0, 60.0, True, 640.0)], 0.5) for index in range(25))
    closed = WaveDetector()
    import math

    assert not any(closed.update(i * 0.1, [Hand(300.0 + 80 * math.sin(i), 240.0, 60.0, False, 640.0)], 0.5) for i in range(25))
    vertical = WaveDetector()
    states = [vertical.update(i * 0.1, [Hand(300.0 + 5 * math.sin(i), 240.0 + 150 * math.sin(i * 1.9), 60.0, True, 640.0)], 0.5)
              for i in range(30)]
    assert not any(states)


def test_a_hand_on_its_way_is_not_an_open_palm_which_would_mean_stop():
    from vision.signs import Hand, WaveDetector

    detector = WaveDetector()
    detector.update(0.0, [Hand(100.0, 240.0, 60.0, True, 640.0)], 0.5)
    detector.update(0.1, [Hand(180.0, 240.0, 60.0, True, 640.0)], 0.5)  # a quick sideways move
    assert detector.moving(0.2) and not detector.moving(1.5)


def test_motion_gestures_count_at_once_while_a_still_gesture_must_be_held():
    from vision.signs import KIND_GESTURE, MOTION_GESTURES, Sign, SignTracker

    assert "Wave_Visitor_Right" in MOTION_GESTURES and "Six_Seven" in MOTION_GESTURES
    tracker = SignTracker()
    assert tracker.update([Sign(KIND_GESTURE, "Wave_Visitor_Right")], 0.0) == [Sign(KIND_GESTURE, "Wave_Visitor_Right")]
    assert SignTracker().update([Sign(KIND_GESTURE, "Victory")], 0.0) == []  # held for 0.7 s first
