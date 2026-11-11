"""Visitor-facing screen: welcome, conversation and public relations pages."""
from __future__ import annotations

from datetime import datetime

from html import escape

from PySide6.QtCore import QEasingCurve, QEvent, QObject, QPropertyAnimation, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QResizeEvent
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QScroller,
    QSizePolicy,
    QSlider,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from app.ai_key import AiKeyController
from app.command_controller import CommandController
from app.content_controller import HOME_MEDIA, ContentController
from app.conversation_controller import ConversationController
from app.mic_array_controller import MicArrayController
from app.hardware_controller import ACTOR_TOUCH, DEFAULT_AMOUNTS, GROUP_MOVE, GROUP_TURN, HardwareController
from app.robot_controller import RobotController
from app.state import ConversationState
from app.vision_controller import VisionController
from app.voice_controller import VoiceController, VoiceStatus
from config.settings import Settings
from services import venues
from robot.command_gateway import CommandResult
from services.conversation_service import (
    LANG_ENGLISH,
    LANG_THAI,
    ConversationResult,
    SOURCE_FALLBACK,
    SOURCE_NO_INFO,
    ConversationService,
    detect_language,
)
from ui.camera_view import CameraView
from ui.thai_wrap import ZERO_WIDTH_SPACE, soft_wrap
from ui.robot_face import (
    MOOD_CALM,
    MOOD_CONFUSED,
    MOOD_CURIOUS,
    MOOD_DELIGHTED,
    MOOD_EXCITED,
    MOOD_HAPPY,
    MOOD_IDLE,
    MOOD_LOVE,
    MOOD_NEUTRAL,
    MOOD_SAD,
    MOOD_SPEAKING,
    MOOD_STARSTRUCK,
    MOOD_THINKING,
    MOOD_WELCOME,
    MOOD_WINK,
    MOOD_WORRIED,
    RobotFace,
)
from ui.showcase_page import ShowcasePage
from ui.widgets import (
    WrappedLabel,
    level_to_percent,
    make_button,
    make_label,
    set_style_property,
)
from vision.interfaces import SourceStatus

STATE_LABELS = {
    ConversationState.IDLE: "พร้อมสนทนา",
    ConversationState.LISTENING: "กำลังฟัง...",
    ConversationState.THINKING: "กำลังประมวลผล...",
    ConversationState.SPEAKING: "กำลังตอบ...",
    ConversationState.ERROR: "เกิดข้อผิดพลาด",
}
MESSAGE_DISPLAY_MS = 6000
# How long a moment's expression stays before the face follows the robot's state again.
GREETING_FACE_MS = 4000
PROBLEM_FACE_MS = 4000
THANKS_FACE_MS = 3000
# The line under the face, for each expression.
FACE_CAPTIONS = {
    MOOD_IDLE: "สวัสดีครับ มีอะไรให้ผมช่วยไหมครับ",
    MOOD_NEUTRAL: "สวัสดีครับ มีอะไรให้ผมช่วยไหมครับ",
    MOOD_HAPPY: "สวัสดีครับ ยินดีต้อนรับครับ",
    MOOD_SPEAKING: "กำลังตอบครับ",
    MOOD_THINKING: "ขอคิดสักครู่นะครับ...",
    MOOD_CONFUSED: "ขอโทษครับ ผมยังไม่แน่ใจ ลองถามอีกครั้งได้ไหมครับ",
    MOOD_WORRIED: "ขออภัยครับ มีบางอย่างขัดข้อง",
    MOOD_DELIGHTED: "ขอบคุณครับ",
    MOOD_STARSTRUCK: "ขอบคุณครับ เยี่ยมไปเลย!",
    MOOD_LOVE: "ผมก็รักทุกคนครับ",
    MOOD_SAD: "ขออภัยครับ ผมจะพยายามให้ดีขึ้น",
    MOOD_WINK: "ยิ้มหน่อยครับ ถ่ายรูปได้เลย",
    MOOD_CALM: "หยุดแล้วครับ",
    MOOD_EXCITED: "สู้ ๆ ครับ!",
    MOOD_CURIOUS: "มีคำถามใช่ไหมครับ ถามได้เลย",
    MOOD_WELCOME: "ยินดีต้อนรับท่านคณะกรรมการครับ",
}
# The face for each hand gesture (MediaPipe's names), shown for GESTURE_FACE_MS,
# through the spoken reply.
GESTURE_MOODS = {
    "Thumb_Up": MOOD_STARSTRUCK,
    "Thumb_Down": MOOD_SAD,
    "Open_Palm": MOOD_CALM,
    "ILoveYou": MOOD_LOVE,
    "Victory": MOOD_WINK,
    "Closed_Fist": MOOD_EXCITED,
    "Pointing_Up": MOOD_CURIOUS,
    "Finger_Gun": MOOD_WELCOME,  # the introduction to the committee
}
GESTURE_FACE_MS = 5000
# A gesture whose face stays for the whole of what the robot says (the introduction is half a minute long).
SPOKEN_FACE_GESTURES = frozenset({"Finger_Gun"})
SPOKEN_FACE_MAX_MS = 90_000
LISTENING_CAPTION = "กำลังฟังอยู่ครับ พูดได้เลย"
# Callers put the speaker in front of the text ("คุณ: ..."); the bubble shows it
# as a small name above instead, and transcript_texts() still returns the full line.
_SPEAKER_PREFIXES = {"user": "คุณ: ", "robot": "หุ่นยนต์: "}
_SPEAKER_NAMES = {"user": "คุณ", "robot": "หุ่นยนต์"}
BUBBLE_MAX_FRACTION = 0.8  # of the transcript's width
BUBBLE_FADE_MS = 240
_CAMERA_CHIP_TEXT = {
    SourceStatus.DISABLED: "ปิด",
    SourceStatus.STARTING: "กำลังเปิด",
    SourceStatus.RUNNING: "พร้อม",
    SourceStatus.UNAVAILABLE: "ไม่พร้อม",
}
_CAMERA_CHIP_STATE = {
    SourceStatus.DISABLED: "",
    SourceStatus.STARTING: "executing",
    SourceStatus.RUNNING: "idle",
    SourceStatus.UNAVAILABLE: "error",
}
# The button shows the language in use; pressing it switches to the other one.
_LANGUAGE_BUTTON_TEXT = {LANG_THAI: "ภาษาไทย", LANG_ENGLISH: "English"}
_VOICE_CHIP_TEXT = {
    VoiceStatus.DISABLED: "ปิด",
    VoiceStatus.LOADING: "กำลังเตรียม",
    VoiceStatus.READY: "พร้อม",
    VoiceStatus.UNAVAILABLE: "ใช้ไม่ได้",
}
# Reuses the robot chip colours: idle = green, executing = amber, error = red.
_VOICE_CHIP_STATE = {
    VoiceStatus.DISABLED: "",
    VoiceStatus.LOADING: "executing",
    VoiceStatus.READY: "idle",
    VoiceStatus.UNAVAILABLE: "error",
}
# The real robot's controls, left to right: each group has a slider (how far) over its buttons.
DRIVE_GROUPS = (
    (GROUP_TURN, "มุมหัน", ("left", "right")),
    (GROUP_MOVE, "ระยะเดิน", ("forward", "backward")),
)
MAX_VISITOR_AMOUNT = 100  # degrees or centimetres, at most, from the visitor screen
_DRIVE_TEXT = {"left": "◀  หันซ้าย", "right": "หันขวา  ▶", "forward": "▲  เดินหน้า", "backward": "▼  ถอยหลัง"}
_AMOUNT_TEXT = {GROUP_TURN: "{0}°", GROUP_MOVE: "{0} ซม."}
_DRIVE_NAME = {"left": "หันซ้าย {0} องศา", "right": "หันขวา {0} องศา",
               "forward": "เดินหน้า {0} ซม.", "backward": "ถอยหลัง {0} ซม."}
NO_ROBOT = "ยังไม่ได้ตั้งค่าการเชื่อมต่อหุ่นยนต์"
_ROBOT_CHIP = {  # MqttStatus value -> (text, chip state)
    "connected": ("พร้อม", "idle"),
    "connecting": ("กำลังเชื่อมต่อ", "executing"),
}


class UserScreen(QWidget):
    admin_requested = Signal()
    feedback_given = Signal(bool)  # the visitor tapped "helpful" (True) or "not quite" (False)

    PAGE_HOME = 0
    PAGE_CHAT = 1

    def __init__(
        self,
        settings: Settings,
        conversation: ConversationController,
        robot: RobotController,
        commands: CommandController,
        content: ContentController,
        voice: VoiceController,
        vision: VisionController,
        conversation_service: ConversationService | None = None,
        ai_key: AiKeyController | None = None,
        parent: QWidget | None = None,
        hardware: HardwareController | None = None,
        mic_array: MicArrayController | None = None,
    ) -> None:
        super().__init__(parent)
        # The visitor screen has its own dark theme, selected by this name in theme.qss.
        self.setObjectName("userScreen")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._settings = settings
        self._hardware = hardware  # the real robot over MQTT; None = not set up
        self._mic_array = mic_array  # None = the microphone chip does not ask whether the array is plugged in
        self._conversation = conversation
        self._robot = robot
        self._commands = commands
        self._content = content
        self._voice = voice
        self._vision = vision
        self._conversation_service = conversation_service

        self._suspended = False
        self._listening_paused = False  # "หยุดสนทนา" pressed; cleared by "เริ่มสนทนา"
        self._shown_venue: str | None = None  # the place the chat is about; None until first shown
        self._greeting_shown: str | None = None  # the robot's last greeting, while nothing else is in the chat
        self.showcase = ShowcasePage(content)
        self._pages = QStackedWidget()
        # The home page (public relations media) is kept but not shown: the robot
        # opens on the conversation and stays there (there is no button to leave it).
        self._pages.addWidget(self._build_home_page())
        self._pages.addWidget(self._build_chat_page())
        self._pages.setCurrentIndex(self.PAGE_CHAT)

        self._message_bar = make_label("", "message", word_wrap=True, thai_wrap=False)
        self._message_bar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._message_timer = QTimer(self)
        self._message_timer.setSingleShot(True)
        self._message_timer.timeout.connect(self._clear_message)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 12, 24, 8)
        layout.setSpacing(8)
        layout.addLayout(self._build_top_bar())
        layout.addWidget(self._pages, 1)
        layout.addWidget(self._message_bar)
        layout.addLayout(self._build_bottom_bar())
        # What is (and is not) recorded, always visible: PDPA asks that people are told.
        self._privacy = make_label("", "privacy", word_wrap=True, thai_wrap=False)
        self._privacy.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._privacy)

        conversation.question_received.connect(self._on_question)
        conversation.answer_ready.connect(self._on_answer)
        conversation.reply_ready.connect(self._on_command_reply)
        commands.notice.connect(self._show_message)
        conversation.error_occurred.connect(self._on_conversation_error)
        conversation.state.state_changed.connect(self._on_state_changed)
        robot.command_finished.connect(self._on_command_finished)
        if hardware is not None:
            hardware.status_changed.connect(self._refresh_robot)
            hardware.state_changed.connect(self._refresh_robot)
        content.content_changed.connect(self._reload_content)
        voice.status_changed.connect(self._on_voice_status)
        voice.speculation_changed.connect(self._on_speculation_changed)
        if mic_array is not None:
            mic_array.changed.connect(self._on_voice_status)
        voice.level_changed.connect(self._on_voice_level)
        voice.notice.connect(self._show_message)
        vision.changed.connect(self._on_vision_changed)
        self._on_voice_status()
        self._on_vision_changed()
        self._on_state_changed(None, conversation.state.state)
        self._refresh_robot()
        self._reload_content()
        self._update_face()

    # --- public API used by MainWindow ---

    def show_page(self, index: int) -> None:
        if index != self.PAGE_CHAT:
            self._conversation.end_session()  # however they left, the conversation is over
        self._suspended = False
        self._pages.setCurrentIndex(index)
        self._sync_showcase()

    def current_page(self) -> int:
        return self._pages.currentIndex()

    def suspend(self) -> None:
        """Silence the visitor screen while it is hidden behind the Admin screen."""
        self._conversation.end_session()
        self._suspended = True
        self._sync_showcase()

    def reset_session(self) -> None:
        """Clear the previous visitor's conversation (the screen stays on the conversation)."""
        self.clear_session()

    def clear_session(self) -> None:
        """Clear the previous visitor's conversation, staying on the page that is open.

        What happens by itself (idle time, the visitor leaving, a scheduled
        announcement) uses this: the screen changes page only when someone asks it to.
        """
        self._conversation.end_session()
        self._conversation.forget()
        self._commands.cancel()
        self._voice.set_language(None)
        self._show_language(LANG_THAI)
        self._clear_transcript()
        self._input.clear()
        self._clear_message()
        self._end_mood_override()

    def play_on_home(self, media_id: int) -> None:
        """Admin's "show now": the playlist starts at this item. The home page that plays
        it is not shown any more, so the screen stays on the conversation."""
        self.showcase.play_item(media_id)

    def queue_media(self, media_id: int) -> None:
        """Play this item next on the home page, without leaving the page that is open."""
        self.showcase.play_item(media_id)

    def can_continue_conversation(self) -> bool:
        """True while the visitor is still looking at the conversation page."""
        return not self._suspended and self.current_page() == self.PAGE_CHAT

    def listen_for_follow_up(self) -> None:
        """After an answer: listen for the next question without a button press."""
        if self._listening_paused:
            return
        self._voice.start_listening(follow_up=True)

    def keep_listening(self) -> None:
        """Always-on listening: open the microphone again whenever the robot is idle.

        Does nothing while "หยุดสนทนา" is in effect, while the microphone is open or not
        working, or while the robot is thinking or speaking (it never listens to itself).
        """
        if self._voice.status is not VoiceStatus.READY or self._voice.is_listening:
            return
        if self._conversation.state.state is not ConversationState.IDLE:
            return
        self.listen_hands_free(quiet=True)

    def greet(self, text: str, aloud: bool = True, language: str = LANG_THAI) -> None:
        """A visitor has come into the service area: greet them on the page that is open, aloud.

        The microphone stays closed until they step up to the robot.
        """
        self._conversation.end_session()
        self._conversation.forget()
        self._clear_transcript()
        self._input.clear()
        self._add_bubble("robot", f"หุ่นยนต์: {text}")
        self._greeting_shown = text
        if language == LANG_ENGLISH:
            self._show_message("Come closer and talk to me, or type your question below.", "info")
        else:
            self._show_message("เข้ามาใกล้ ๆ แล้วพูดกับผมได้เลย หรือพิมพ์คำถามด้านล่าง", "info")
        self._conversation.say(text, aloud=aloud)
        self._show_mood(MOOD_HAPPY, GREETING_FACE_MS)

    def show_sign_response(self, shown: str, text: str) -> None:
        """A card or gesture was recognised: show what the Admin set for it, on the page that is open."""
        self._conversation.end_session()
        self._add_bubble("user", f"คุณแสดง: {shown}")
        if text:
            self._add_bubble("robot", f"หุ่นยนต์: {text}")
            self._conversation.say(text)
            if self.current_page() != self.PAGE_CHAT:
                self._show_message(text, "info")  # the conversation is not on screen

    def announce(self, text: str, media_id: int | None) -> None:
        """A scheduled public relations message, on the page that is open."""
        self.clear_session()
        if media_id is not None:
            self.showcase.play_item(media_id)
        if text:
            self._show_message(text, "info")
            self._conversation.say(text)

    def react_to_gesture(self, code: str, shown: str, text: str, speak: bool) -> None:
        """A hand gesture was recognised: its face for a moment, and ``text`` spoken if ``speak``.

        The robot does not speak over its own answer (``speak`` False): the face alone answers.
        """
        if speak and text:
            self._conversation.end_session()
            self._add_bubble("user", f"คุณแสดง: {shown}")
            self._add_bubble("robot", f"หุ่นยนต์: {text}")
            self._conversation.reply_to_gesture(text)
            if self.current_page() != self.PAGE_CHAT:
                self._show_message(text, "info")  # the conversation is not on screen
        mood = GESTURE_MOODS.get(code)
        if mood is not None:
            whole_speech = bool(speak and text) and code in SPOKEN_FACE_GESTURES
            self._show_mood(mood, SPOKEN_FACE_MAX_MS if whole_speech else GESTURE_FACE_MS)
            self._mood_until_idle = whole_speech  # ends by itself when the robot has finished speaking

    def notify(self, text: str, level: str = "info") -> None:
        self._show_message(text, level)
        if level == "error":
            self._show_mood(MOOD_WORRIED, PROBLEM_FACE_MS)

    def feedback_recorded(self, positive: bool) -> None:
        self._feedback_row.hide()
        self._show_mood(MOOD_DELIGHTED if positive else MOOD_CONFUSED, THANKS_FACE_MS)
        self._show_message(
            "ขอบคุณสำหรับคำติชมครับ" if positive else "ขอบคุณครับ ผมจะนำไปปรับปรุง", "info"
        )

    def listen_hands_free(self, quiet: bool = False) -> None:
        """The visitor is at conversation distance: listen without a button press.

        It opens a conversation like the start button does, so what they say is
        answered aloud. If voice input is not ready this does nothing; they can
        still press the button or type. ``quiet``: silence just ends it, without
        asking them to try again.
        """
        if self._listening_paused:
            return  # "หยุดสนทนา" was pressed: only "เริ่มสนทนา" opens the microphone again
        if self._voice.is_listening:
            return  # already open (the start button, or a trigger before this one)
        self._conversation.begin_session()
        if not self._voice.start_listening(follow_up=quiet):
            self._conversation.end_session()

    def transcript_texts(self) -> list[str]:
        return [
            label.property("transcript")
            for label in self._bubbles.findChildren(QLabel)
            if label.property("role") == "bubble"
        ]

    # --- layout ---

    def _build_top_bar(self) -> QHBoxLayout:
        bar = QHBoxLayout()
        bar.setSpacing(10)
        # What the robot answers about: the visitor picks the place (its knowledge base).
        # Greetings and general questions are answered whichever is picked.
        self._venue_choice = QComboBox()
        self._venue_choice.setProperty("role", "topic")
        self._venue_choice.setToolTip("เลือกว่าจะถามเรื่องอะไร")
        for venue in venues.VENUES:
            self._venue_choice.addItem(venue.short, venue.id)
        self._venue_choice.activated.connect(  # the visitor's pick only, not _show_venue's
            lambda index: self._choose_venue(self._venue_choice.itemData(index))
        )
        bar.addWidget(self._venue_choice)
        bar.addStretch(1)
        # One chip per subsystem, each from that subsystem's real status.
        self._camera_chip = make_label("", "chip")
        self._voice_chip = make_label("", "chip")
        self._robot_chip = make_label("", "chip")
        for chip in (self._camera_chip, self._voice_chip, self._robot_chip):
            bar.addWidget(chip)
        bar.addSpacing(6)
        bar.addWidget(make_button("ผู้ดูแล", "ghost", self.admin_requested.emit))
        return bar

    def _build_bottom_bar(self) -> QHBoxLayout:
        """The real robot's controls, then its stop button as tall as they are."""
        bar = QHBoxLayout()
        bar.setSpacing(16)
        self._command_bar = self._build_command_bar()
        bar.addWidget(self._command_bar, 5)
        stop = make_button("หยุดหุ่นยนต์", "danger", self._stop_robot)
        stop.setToolTip("หยุดหุ่นยนต์ทันที")
        stop.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        bar.addWidget(stop, 1)
        return bar

    def _build_home_page(self) -> QWidget:
        """Home: public relations content on the left, the invitation to talk on the right.

        The big area shows what the Admin chose — the media playlist with
        announcements, or the live camera. When media is shown, the camera sits
        small above the invitation. ``_arrange_home`` decides, and falls back
        sensibly when the chosen content is not available.
        """
        page = QWidget()
        layout = QHBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(20)

        self._home_camera = CameraView(self._vision)
        self._home_main = QStackedWidget()
        self._home_main.addWidget(self.showcase)
        self._home_main.addWidget(self._home_camera)
        layout.addWidget(self._home_main, 3)

        self._home_layout = layout
        self._home_side = QFrame()
        self._home_side.setProperty("role", "panel")
        self._home_side.setMaximumWidth(720)
        panel = QVBoxLayout(self._home_side)
        panel.setContentsMargins(18, 18, 18, 18)
        panel.setSpacing(8)
        self._home_camera_small = CameraView(self._vision, compact=True)
        # The camera takes whatever height the text and the button leave over.
        self._home_camera_small.setMinimumHeight(80)
        panel.addWidget(self._home_camera_small, 1)
        panel.addStretch(0)
        panel.addSpacing(8)
        eyebrow = make_label("ผู้ช่วยต้อนรับอัจฉริยะ", "eyebrow")
        eyebrow.setAlignment(Qt.AlignmentFlag.AlignCenter)
        panel.addWidget(eyebrow)
        self._welcome_label = WrappedLabel()
        self._welcome_label.setProperty("role", "hero")
        self._welcome_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        panel.addWidget(self._welcome_label)
        self._home_hint = WrappedLabel()
        self._home_hint.setProperty("role", "lead")
        self._home_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        panel.addWidget(self._home_hint)
        panel.addSpacing(10)
        panel.addWidget(make_button("เริ่มสนทนา", "hero", self._start_conversation))
        # When the camera is hidden this stretch centres the text and button.
        self._home_bottom_stretch = panel.count()
        panel.addStretch(0)
        self._home_panel = panel
        layout.addWidget(self._home_side, 2)
        return page

    def _arrange_home(self) -> None:
        vision = self._vision
        has_picture = not vision.frame.isNull()
        camera_available = has_picture or vision.status is SourceStatus.STARTING
        wants_media = self._content.home_display() == HOME_MEDIA

        if self.showcase.has_content() and (wants_media or not camera_available):
            main = self.showcase
        elif camera_available:
            main = self._home_camera
        else:
            main = None  # nothing to show: the invitation stands alone, centred

        self._home_main.setVisible(main is not None)
        if main is not None:
            self._home_main.setCurrentWidget(main)
        small_camera = main is self.showcase and has_picture
        self._home_camera_small.setVisible(small_camera)
        # With the small camera the column is filled top to bottom; without it
        # the text and button sit in the middle.
        self._home_panel.setStretch(1, 0 if small_camera else 1)
        self._home_panel.setStretch(self._home_bottom_stretch, 0 if small_camera else 1)
        self._home_side.setMinimumWidth(340 if main is not None else 640)
        self._home_layout.setAlignment(
            self._home_side,
            Qt.AlignmentFlag(0) if main is not None else Qt.AlignmentFlag.AlignHCenter,
        )
        self._sync_showcase()

    def _sync_showcase(self) -> None:
        """Media plays only while it is actually on screen."""
        on_screen = (
            not self._suspended
            and self.current_page() == self.PAGE_HOME
            and self._home_main.isVisibleTo(self)
            and self._home_main.currentWidget() is self.showcase
        )
        if on_screen:
            self.showcase.activate()
        else:
            self.showcase.deactivate()

    def _build_chat_page(self) -> QWidget:
        page = QWidget()
        layout = QHBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(20)

        # Left card: conversation status and controls, transcript, typed input.
        conversation_card = QFrame()
        conversation_card.setProperty("role", "panel")
        left = QVBoxLayout(conversation_card)
        left.setContentsMargins(18, 14, 18, 14)
        left.setSpacing(10)

        status_row = QHBoxLayout()
        status_row.setSpacing(10)
        state_column = QVBoxLayout()
        state_column.setSpacing(6)
        self._state_label = make_label("", "state")
        state_column.addWidget(self._state_label, 0, Qt.AlignmentFlag.AlignLeft)
        # The live microphone level, shown only while the robot is listening.
        self._level_bar = QProgressBar()
        self._level_bar.setRange(0, 100)
        self._level_bar.setTextVisible(False)
        self._level_bar.setFixedHeight(6)
        retain = self._level_bar.sizePolicy()
        retain.setRetainSizeWhenHidden(True)
        self._level_bar.setSizePolicy(retain)
        state_column.addWidget(self._level_bar)
        status_row.addLayout(state_column, 1)
        # The language in use, following the conversation. Pressing it also tells speech
        # recognition to expect English; typed questions are detected by themselves.
        self._language_shown = LANG_THAI
        self._language_button = make_button(
            _LANGUAGE_BUTTON_TEXT[LANG_THAI], "secondary", self._toggle_language
        )
        status_row.addWidget(self._language_button)
        status_row.addWidget(make_button("หยุดสนทนา", "secondary", self._stop_conversation))
        # One button: starts a turn, and while listening ends it ("I have finished speaking").
        self._talk_button = make_button("เริ่มสนทนา", "talk", self._on_talk_button)
        status_row.addWidget(self._talk_button)
        left.addLayout(status_row)

        # The transcript is a column of plain-text bubbles (one per message).
        self._bubbles = QWidget()
        self._bubbles.setObjectName("transcriptPanel")
        self._bubble_layout = QVBoxLayout(self._bubbles)
        self._bubble_layout.setContentsMargins(16, 16, 16, 16)
        self._bubble_layout.setSpacing(14)
        self._bubble_layout.addStretch(1)
        self._transcript_scroll = _scroll_area(self._bubbles)
        self._transcript_scroll.setProperty("role", "transcript")
        self._transcript_scroll.viewport().installEventFilter(self)
        bar = self._transcript_scroll.verticalScrollBar()
        self._latest_row: QWidget | None = None  # the newest message: a long one is shown from its top
        bar.rangeChanged.connect(lambda _low, high: self._scroll_to_latest(bar, high))
        # Before the first message: an invitation instead of an empty box.
        self._transcript_stack = QStackedWidget()
        self._transcript_stack.addWidget(self._build_empty_transcript())
        self._transcript_stack.addWidget(self._transcript_scroll)
        left.addWidget(self._transcript_stack, 1)

        # Shown after each answer until the visitor rates it (by touch or by a thumb gesture).
        self._feedback_row = QFrame()
        self._feedback_row.setProperty("role", "inline-card")
        feedback = QHBoxLayout(self._feedback_row)
        feedback.setContentsMargins(16, 6, 8, 6)
        feedback.addWidget(make_label("คำตอบนี้ช่วยได้ไหมครับ", "hint"), 1)
        feedback.addWidget(make_button("ช่วยได้", "ghost", lambda: self.feedback_given.emit(True)))
        feedback.addWidget(make_button("ยังไม่ตรง", "ghost", lambda: self.feedback_given.emit(False)))
        self._feedback_row.hide()
        left.addWidget(self._feedback_row)

        input_row = QHBoxLayout()
        input_row.setSpacing(10)
        self._input = QLineEdit()
        self._input.setPlaceholderText("พิมพ์คำถามที่นี่ แล้วกด ส่ง")
        self._input.setMaxLength(500)
        self._input.returnPressed.connect(self._send_typed_question)
        input_row.addWidget(self._input, 1)
        send = make_button("ส่ง", "primary", self._send_typed_question)
        send.setMinimumWidth(110)
        input_row.addWidget(send)
        left.addLayout(input_row)
        layout.addWidget(conversation_card, 3)

        # Right card: the robot's face on top and the live camera under it, the same
        # height each (the robot's buttons are in the bottom bar). Without a camera
        # picture the face takes it all, so the card never looks empty.
        service_card = QFrame()
        service_card.setProperty("role", "panel")
        right = QVBoxLayout(service_card)
        right.setContentsMargins(14, 14, 14, 14)
        right.setSpacing(10)
        self._face = RobotFace()
        self._face_caption = WrappedLabel()
        self._face_caption.setProperty("role", "lead")
        self._face_caption.setAlignment(Qt.AlignmentFlag.AlignCenter)
        # Live view: the visitor can see that the robot is looking at them.
        self._chat_camera = CameraView(self._vision, compact=True)
        self._chat_camera.setMinimumHeight(180)
        self._face.setMinimumHeight(180)
        right.addWidget(self._face, 1)
        right.addWidget(self._face_caption)
        right.addWidget(self._chat_camera, 1)
        # A moment's expression (a greeting, an error) before the face follows the state again.
        self._mood_until_idle = False  # the face lasts as long as the robot is speaking (see react_to_gesture)
        self._mood_override: str | None = None
        self._mood_timer = QTimer(self)
        self._mood_timer.setSingleShot(True)
        self._mood_timer.timeout.connect(self._end_mood_override)

        layout.addWidget(service_card, 2)
        return page

    def _build_command_bar(self) -> QWidget:
        """The real robot's buttons, next to its stop button: turn, move, and how far.

        They send over MQTT exactly as the Admin page does (the same topics, angle and
        distance). They sit in a widget, not a nested layout: a nested layout keeps the
        button heights it measured before the stylesheet applied, and the buttons then overlap.
        """
        bar = QFrame()
        bar.setProperty("role", "segmented")
        row = QHBoxLayout(bar)
        row.setContentsMargins(16, 10, 16, 10)
        row.setSpacing(24)
        self._drive_buttons: dict[str, QPushButton] = {}
        self._amount_sliders: dict[str, QSlider] = {}
        self._amount_labels: dict[str, QLabel] = {}
        for group, title, directions in DRIVE_GROUPS:
            column = QVBoxLayout()
            column.setSpacing(6)
            # How far: a small slider above the group's two buttons, the exact value beside it.
            amount = QHBoxLayout()
            amount.setSpacing(12)
            amount.addWidget(make_label(title, "hint"))
            slider = QSlider(Qt.Orientation.Horizontal)
            slider.setRange(1, MAX_VISITOR_AMOUNT)
            slider.setTracking(False)  # saved when let go; the number follows while dragging
            slider.sliderMoved.connect(lambda value, g=group: self._show_amount(g, value))
            slider.valueChanged.connect(lambda value, g=group: self._set_amount(g, value))
            self._amount_sliders[group] = slider
            amount.addWidget(slider, 1)
            value = make_label("", "amount")
            value.setMinimumWidth(78)
            value.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self._amount_labels[group] = value
            amount.addWidget(value)
            column.addLayout(amount)
            buttons = QHBoxLayout()
            buttons.setSpacing(8)
            for direction in directions:
                button = make_button(_DRIVE_TEXT[direction], "command", lambda d=direction: self._drive(d))
                self._drive_buttons[direction] = button
                buttons.addWidget(button, 1)
            column.addLayout(buttons)
            row.addLayout(column, 1)
        return bar

    def _build_empty_transcript(self) -> QWidget:
        empty = QFrame()
        empty.setProperty("role", "empty-state")
        layout = QVBoxLayout(empty)
        layout.setContentsMargins(24, 12, 24, 12)
        layout.setSpacing(6)
        layout.addStretch(1)
        texts = (
            ("พร้อมช่วยเหลือ", "eyebrow"),
            ("สวัสดีครับ มีอะไรให้ผมช่วยไหมครับ", "empty-title"),
            ("กด  เริ่มสนทนา  แล้วพูดคำถามได้เลย\n"
             "หรือพิมพ์คำถามด้านล่าง", "empty-body"),
        )
        for text, role in texts:
            label = WrappedLabel(text)
            label.setProperty("role", role)
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            layout.addWidget(label)
        layout.addStretch(1)
        return empty

    # --- content ---

    def _reload_content(self) -> None:
        self._arrange_home()  # the playlist or the Admin's choice for the home page may have changed
        self._welcome_label.setText(self._content.welcome_message())
        notice = self._content.privacy_notice()
        self._privacy.setText(notice)
        self._privacy.setVisible(bool(notice))
        self._show_venue()  # the Admin may have switched the place

    # --- conversation ---

    def _stop_conversation(self) -> None:
        """"หยุดสนทนา": silence now, and the microphone stays closed until "เริ่มสนทนา".

        Without this the camera (someone close) or the end of a greeting opened it
        again by itself, and whatever was still being said nearby got an answer.
        """
        self._listening_paused = True
        self._conversation.end_session()
        self._show_message("หยุดฟังแล้ว กด \"เริ่มสนทนา\" เมื่อต้องการถามต่อ", "info")

    @property
    def listening_paused(self) -> bool:
        """The visitor pressed "หยุดสนทนา": nothing opens the microphone but "เริ่มสนทนา"."""
        return self._listening_paused

    def _start_conversation(self) -> None:
        self._listening_paused = False
        self.show_page(self.PAGE_CHAT)
        self._conversation.begin_session()
        if self._voice.start_listening():
            return
        if self._conversation.state.state is ConversationState.ERROR:
            return  # the microphone failed to open; the reason is already on screen
        # Voice input is not usable right now: fall back to typing.
        self._conversation.start_listening()
        self._input.setFocus()
        self._show_message(
            f"ไมโครโฟน: {self._voice.status_text} — พิมพ์คำถามหรือเลือกจากปุ่มลัดได้เลย", "info"
        )

    def _show_language(self, language: str) -> None:
        self._language_shown = language
        self._language_button.setText(_LANGUAGE_BUTTON_TEXT[language])

    def _follow_language(self, language: str) -> None:
        """The robot has just answered in ``language``: the button shows it by itself.

        Speech recognition stays on detecting both languages, or a visitor who
        answered in English could not go back to Thai by speaking. Only a press
        fixes it to English, and a Thai turn releases that.
        """
        self._show_language(language)
        if language == LANG_THAI:
            self._voice.set_language(None)

    def _toggle_language(self) -> None:
        language = LANG_THAI if self._language_shown == LANG_ENGLISH else LANG_ENGLISH
        self._voice.set_language(LANG_ENGLISH if language == LANG_ENGLISH else None)
        self._show_language(language)
        if self._only_the_greeting_is_shown():
            # Nobody has said anything yet: the greeting changes language with the button.
            self.greet(self._content.greeting_at(datetime.now(), language), language=language)
        elif language == LANG_ENGLISH:
            self._show_message("English mode: please speak English. I will answer in English.", "info")
        else:
            self._show_message("โหมดภาษาไทย: พูดภาษาไทยได้เลยครับ", "info")

    def _only_the_greeting_is_shown(self) -> bool:
        """The chat is empty or holds only the robot's greeting: no conversation to keep."""
        texts = self.transcript_texts()
        return not texts or (self._greeting_shown is not None and texts == [f"หุ่นยนต์: {self._greeting_shown}"])

    def _on_talk_button(self) -> None:
        if self._voice.is_listening:
            self._voice.finish_listening()
        else:
            self._start_conversation()

    def _on_voice_status(self) -> None:
        """The microphone chip: ready only while the microphone array is plugged in (MIC_ARRAY_REQUIRED).

        Listening still works through whatever microphone is chosen when the array is out; the chip
        says that the array, the microphone the robot is meant to use, is not there.
        """
        status = self._voice.status
        array = self._mic_array
        if array is not None and status is VoiceStatus.READY:
            if array.connected:
                text, state = "พร้อม", "idle"
                tip = f"ต่อไมโครโฟนอาเรย์อยู่: {array.device} — {self._voice.status_text}"
            else:
                text, state = "ไม่พร้อม", "error"
                tip = f"ยังไม่ได้ต่อไมโครโฟนอาเรย์ XVF3800 (ตอนนี้ใช้ไมโครโฟนที่เลือกไว้แทน) — {self._voice.status_text}"
        else:
            text, state, tip = _VOICE_CHIP_TEXT[status], _VOICE_CHIP_STATE[status], self._voice.status_text
        self._voice_chip.setText(f"ไมโครโฟน: {text}")
        self._voice_chip.setToolTip(tip)
        set_style_property(self._voice_chip, "state", state)

    def _on_vision_changed(self) -> None:
        vision = self._vision
        status = vision.status
        self._camera_chip.setText(f"กล้อง: {_CAMERA_CHIP_TEXT[status]}")
        self._camera_chip.setToolTip(vision.detail)
        set_style_property(self._camera_chip, "state", _CAMERA_CHIP_STATE[status])
        has_picture = not vision.frame.isNull()
        self._chat_camera.setVisible(has_picture)
        self._home_camera.set_placeholder("กำลังเปิดกล้อง...")
        self._arrange_home()

        if not has_picture:
            caption = ""
            hint = "กดปุ่มด้านล่างเพื่อเริ่มคุยกับผมได้เลยครับ"
        elif vision.engaged:
            caption = "พูดกับผมได้เลยครับ"
            hint = "ผมฟังอยู่ครับ ถามได้เลย"
        elif vision.present:
            caption = "สวัสดีครับ เข้ามาใกล้อีกนิด แล้วพูดกับผมได้เลย"
            hint = "เข้ามาใกล้ ๆ แล้วพูดได้เลย ไม่ต้องกดปุ่ม"
        elif vision.distance_m is not None:
            caption = f"เห็นคุณแล้ว ห่าง {vision.distance_m:.1f} ม. — เข้ามาใกล้อีกนิดได้เลยครับ"
            hint = "เดินเข้ามาใกล้ ๆ แล้วพูดได้เลย ไม่ต้องกดปุ่ม"
        else:
            caption = "เดินเข้ามาหาผมได้เลยครับ"
            hint = "เดินเข้ามาใกล้ ๆ แล้วพูดได้เลย ไม่ต้องกดปุ่ม"
        self._home_camera.set_caption(caption)
        # The small view has no room for a sentence; the hint below it says the same.
        self._home_hint.setText(hint)
        self._update_face()  # somebody arriving wakes the face up

    def _on_voice_level(self, level: float) -> None:
        self._level_bar.setValue(level_to_percent(level))

    def _send_typed_question(self) -> None:
        text = self._input.text()
        self._input.clear()
        self._conversation.ask(text)

    def _on_question(self, text: str) -> None:
        self.show_page(self.PAGE_CHAT)
        self._add_bubble("user", f"คุณ: {text}")

    def _on_answer(self, result: ConversationResult) -> None:
        self._add_bubble("robot", f"หุ่นยนต์: {result.display_text}")
        self._feedback_row.show()
        self._follow_language(result.language)
        if result.source in (SOURCE_FALLBACK, SOURCE_NO_INFO):  # nothing to answer with: it shows while saying so
            self._show_mood(MOOD_CONFUSED, PROBLEM_FACE_MS)

    def _on_conversation_error(self, message: str) -> None:
        self._show_message(message, "error")
        self._show_mood(MOOD_WORRIED, PROBLEM_FACE_MS)

    def _add_bubble(self, speaker: str, text: str) -> None:
        prefix = _SPEAKER_PREFIXES.get(speaker, "")
        shown = text[len(prefix):] if prefix and text.startswith(prefix) else text
        bubble = _Bubble(shown)
        bubble.setProperty("role", "bubble")
        bubble.setProperty("speaker", speaker)
        bubble.setProperty("transcript", text)
        bubble.setMaximumWidth(self._bubble_max_width())

        # Visitor on the right, robot on the left, each with a small name above.
        # The bubble is pushed aside by a stretch rather than aligned: an aligned
        # item gets its height for the full row width and loses its wrapped lines.
        row = QWidget()
        column = QVBoxLayout(row)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(4)
        mine = speaker == "user"
        side = Qt.AlignmentFlag.AlignRight if mine else Qt.AlignmentFlag.AlignLeft
        name = _SPEAKER_NAMES.get(speaker, "")
        if speaker == "robot":
            persona = self._content.persona()
            if persona.has_name:  # "ไอร่า" (or "Aira" in English mode) instead of "หุ่นยนต์"
                name = persona.english_name if self._language_shown == LANG_ENGLISH else persona.thai_name
        column.addWidget(make_label(name, "speaker"), 0, side)
        line = QHBoxLayout()
        line.setContentsMargins(0, 0, 0, 0)
        if mine:
            line.addStretch(1)
        line.addWidget(bubble)
        if not mine:
            line.addStretch(1)
        column.addLayout(line)
        # Insert above the trailing stretch that keeps messages packed at the top.
        self._bubble_layout.insertWidget(self._bubble_layout.count() - 1, row)
        self._transcript_stack.setCurrentWidget(self._transcript_scroll)
        self._latest_row = row
        self._fade_in(row)

    def _scroll_to_latest(self, bar, bottom: int) -> None:
        """Keep the newest message in view: at the bottom, or from its first line if it is taller than the view."""
        row = self._latest_row
        view = self._transcript_scroll.viewport().height()
        if row is not None and row.height() > view - 16:
            bar.setValue(max(0, row.y() - 8))
        else:
            bar.setValue(bottom)

    @staticmethod
    def _fade_in(row: QWidget) -> None:
        """A message eases in instead of appearing at once (the effect is dropped when it is done)."""
        effect = QGraphicsOpacityEffect(row)
        effect.setOpacity(0.0)
        row.setGraphicsEffect(effect)
        animation = QPropertyAnimation(effect, b"opacity", row)
        animation.setDuration(BUBBLE_FADE_MS)
        animation.setStartValue(0.0)
        animation.setEndValue(1.0)
        animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        animation.finished.connect(lambda: row.setGraphicsEffect(None))
        animation.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)

    def _clear_transcript(self) -> None:
        self._feedback_row.hide()
        self._latest_row = None
        while self._bubble_layout.count() > 1:  # everything but the trailing stretch
            row = self._bubble_layout.takeAt(0).widget()
            if row is not None:
                row.setParent(None)
                row.deleteLater()
        self._transcript_stack.setCurrentIndex(0)

    def _bubble_max_width(self) -> int:
        width = self._transcript_scroll.viewport().width() - 2 * 16
        return max(200, int(width * BUBBLE_MAX_FRACTION))

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if watched is self._transcript_scroll.viewport() and event.type() == QEvent.Type.Resize:
            width = self._bubble_max_width()
            for bubble in self._bubbles.findChildren(_Bubble):
                bubble.setMaximumWidth(width)
        return super().eventFilter(watched, event)

    def _on_speculation_changed(self) -> None:
        """The robot is working out what was said while a pause goes on, or the visitor spoke again."""
        self._on_state_changed(None, self._conversation.state.state)

    def _on_state_changed(self, _old: object, new: ConversationState) -> None:
        listening_by_voice = new is ConversationState.LISTENING and self._voice.is_listening
        text = "กำลังฟัง... พูดได้เลย" if listening_by_voice else STATE_LABELS[new]
        if listening_by_voice and self._voice.speculating:
            text = STATE_LABELS[ConversationState.THINKING]  # as for any answer being worked out: no extra words
        self._state_label.setText(text)
        # At the first sign of silence the chip is already the yellow "processing" one; it goes
        # back to listening by itself if the visitor speaks on.
        shown = ConversationState.THINKING if listening_by_voice and self._voice.speculating else new
        set_style_property(self._state_label, "state", shown.value)
        self._level_bar.setVisible(listening_by_voice)
        if not listening_by_voice:
            self._level_bar.setValue(0)
        self._talk_button.setText("พูดจบแล้ว" if listening_by_voice else "เริ่มสนทนา")
        set_style_property(self._talk_button, "active", "true" if listening_by_voice else "")
        if new in (ConversationState.LISTENING, ConversationState.THINKING):
            self._end_mood_override()  # a new question: the moment has passed
        elif new is ConversationState.IDLE and self._mood_until_idle:
            self._end_mood_override()  # the introduction is over
        else:
            self._update_face()

    # --- the robot's face ---

    def face_mood(self) -> str:
        return self._face.mood

    def _show_mood(self, mood: str, duration_ms: int) -> None:
        """An expression for a moment (a greeting, a problem, thanks), then back to the state."""
        self._mood_override = mood
        self._mood_until_idle = False
        self._mood_timer.start(duration_ms)
        self._update_face()

    def _end_mood_override(self) -> None:
        self._mood_timer.stop()
        self._mood_override = None
        self._update_face()

    def _update_face(self) -> None:
        state = self._conversation.state.state
        if self._mood_override is not None:
            mood = self._mood_override
        elif state is ConversationState.THINKING or (
            state is ConversationState.LISTENING and self._voice.speculating
        ):
            mood = MOOD_THINKING
        elif state is ConversationState.SPEAKING:
            mood = MOOD_SPEAKING
        elif state is ConversationState.ERROR:
            mood = MOOD_WORRIED
        elif (state is ConversationState.LISTENING or self._conversation.session_active
              or self._vision.present):
            mood = MOOD_NEUTRAL
        else:
            mood = MOOD_IDLE  # nobody is using the robot
        self._face.set_mood(mood)
        listening = state is ConversationState.LISTENING and mood == MOOD_NEUTRAL
        self._face_caption.setText(LISTENING_CAPTION if listening else FACE_CAPTIONS[mood])

    # --- robot ---

    def _on_command_reply(self, text: str) -> None:
        self._feedback_row.hide()
        self._add_bubble("robot", f"หุ่นยนต์: {text}")
        self._follow_language(detect_language(text))

    def _stop_robot(self) -> None:
        self._conversation.end_session()  # a visitor pressing stop means the voice as well
        stopped = self._robot.stop(source="touch")  # anything the voice commands started
        if self._hardware is not None:
            reason = self._hardware.send_stop(ACTOR_TOUCH)
            stopped, detail = not reason, f" ({reason})" if reason else ""
        else:
            detail = ""
        if stopped:
            self._show_message("ส่งคำสั่งหยุดหุ่นยนต์แล้ว", "info")
        else:
            self._show_message(
                f"ส่งคำสั่งหยุดไม่สำเร็จ{detail} กรุณาใช้ปุ่มหยุดฉุกเฉินที่ตัวหุ่นยนต์", "error"
            )

    def _drive(self, direction: str) -> None:
        """One of the ◀ ▲ ▼ ▶ buttons: send it to the real robot, or say why it cannot be sent."""
        hardware = self._hardware
        reason = hardware.send_from_visitor(direction) if hardware is not None else NO_ROBOT
        self._refresh_robot()  # greyed until the pause between commands is over
        if reason:
            self._show_message(f"ยังสั่งหุ่นยนต์ไม่ได้: {reason}", "error")
            return
        amount = hardware.amount(GROUP_TURN if direction in ("left", "right") else GROUP_MOVE)
        self._show_message(f"ส่งคำสั่ง{_DRIVE_NAME[direction].format(amount)} ไปที่หุ่นยนต์แล้ว", "info")
        self._show_mood(MOOD_DELIGHTED, THANKS_FACE_MS)

    def _choose_venue(self, venue_id: str) -> None:
        """The visitor picked what to ask about: answers come from that place's knowledge base."""
        if venue_id != self._content.venue_id():
            # content_changed follows: the buttons show it, and _show_venue starts the chat over.
            self._content.set_venue(venue_id)
        self._show_venue()

    def _show_venue(self) -> None:
        """Show the place in use in the picker. A different place than before starts a new chat:
        the old conversation is cleared and the robot greets as the new place's robot.

        It follows the place however it was changed (the visitor's pick, or the Admin's).
        """
        venue_id = self._content.venue_id()
        index = self._venue_choice.findData(venue_id)
        if index >= 0 and index != self._venue_choice.currentIndex():
            self._venue_choice.setCurrentIndex(index)
        shown, self._shown_venue = self._shown_venue, venue_id
        if shown is not None and shown != venue_id:
            self.clear_session()  # questions about the other place are not followed up on
            self.greet(self._content.greeting_at(datetime.now()))

    def _show_amount(self, group: str, value: int) -> None:
        self._amount_labels[group].setText(_AMOUNT_TEXT[group].format(value))

    def _set_amount(self, group: str, value: int) -> None:
        """A slider was let go (or moved by a tap): the robot's buttons send this much from now."""
        if self._hardware is not None:
            self._hardware.set_amount(group, value)
        self._refresh_robot()

    def _refresh_robot(self, *_args) -> None:
        """The robot chip, sliders and buttons, from the real robot's link."""
        hardware = self._hardware
        if hardware is None:
            text, state, tip = "ไม่พร้อม", "error", NO_ROBOT
            amounts = DEFAULT_AMOUNTS
        else:
            text, state = _ROBOT_CHIP.get(hardware.status.status.value, ("ไม่พร้อม", "error"))
            tip = hardware.status.detail
            amounts = {GROUP_TURN: hardware.amount(GROUP_TURN), GROUP_MOVE: hardware.amount(GROUP_MOVE)}
        self._robot_chip.setText(f"หุ่นยนต์: {text}")
        self._robot_chip.setToolTip(tip)
        set_style_property(self._robot_chip, "state", state)
        for group, slider in self._amount_sliders.items():
            if hardware is not None:
                # 100 at most here, and not past what the hardware settings allow.
                slider.setMaximum(min(MAX_VISITOR_AMOUNT, hardware.amount_limit(group)))
            if not slider.isSliderDown():
                slider.blockSignals(True)
                slider.setValue(amounts[group])
                slider.blockSignals(False)
                self._show_amount(group, amounts[group])
            slider.setEnabled(hardware is not None)
        for direction, button in self._drive_buttons.items():
            why = hardware.visitor_refusal(direction) if hardware is not None else NO_ROBOT
            # Still pressable when it cannot be sent: a press says why, a grey button alone would not.
            set_style_property(button, "available", "false" if why else "true")
            button.setToolTip(why)

    def _on_command_finished(self, result: CommandResult) -> None:
        self._show_message(result.message, "info" if result.ok else "error")
        self._show_mood(MOOD_DELIGHTED if result.ok else MOOD_WORRIED,
                        THANKS_FACE_MS if result.ok else PROBLEM_FACE_MS)

    def _show_message(self, text: str, level: str) -> None:
        self._message_bar.setText(text)
        set_style_property(self._message_bar, "level", level)
        self._message_timer.start(MESSAGE_DISPLAY_MS)

    def _clear_message(self) -> None:
        self._message_bar.setText("")
        set_style_property(self._message_bar, "level", "")  # no empty coloured strip


BUBBLE_LINE_HEIGHT_PERCENT = 132  # Thai tone marks and vowels above the line need the room


class _Bubble(QLabel):
    """One message: as wide as its text, up to its maximum width, then it wraps between words.

    The text is shown as HTML only for its line spacing; it is escaped, so nothing in a
    message is ever read as markup.
    """

    def __init__(self, text: str) -> None:
        super().__init__()
        self._plain = soft_wrap(text)  # Thai words get places to break: Qt has none of its own
        self.setTextFormat(Qt.TextFormat.RichText)
        self.setText(
            f'<div style="line-height:{BUBBLE_LINE_HEIGHT_PERCENT}%">'
            + escape(self._plain).replace("\n", "<br>") + "</div>"
        )
        self.setWordWrap(True)
        # Padding as contents margins so the size hints below include it.
        self.setContentsMargins(20, 14, 20, 14)
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Preferred)
        self.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)

    def _natural_width(self) -> int:
        metrics = self.fontMetrics()
        lines = self._plain.replace(ZERO_WIDTH_SPACE, "").split("\n")
        widest = max((metrics.horizontalAdvance(line) for line in lines), default=0)
        margins = self.contentsMargins()
        return widest + margins.left() + margins.right() + 12  # border and rounding slack

    def sizeHint(self) -> QSize:
        width = min(self._natural_width(), self.maximumWidth())
        return QSize(width, self.heightForWidth(width))

    def minimumSizeHint(self) -> QSize:
        # The height it needs at the width it really has (as WrappedLabel does);
        # measured at the narrow minimum width it would leave a gap under the transcript.
        current = self.width() if self.width() > 0 else self.sizeHint().width()
        return QSize(min(self._natural_width(), self.maximumWidth(), 160), self.heightForWidth(current))

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        if event.size().width() != event.oldSize().width():
            self.updateGeometry()

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        # The stylesheet's font arrives after the bubble is built: measure again with it.
        if event.type() in (QEvent.Type.FontChange, QEvent.Type.StyleChange):
            self.updateGeometry()


def _scroll_area(content: QWidget) -> QScrollArea:
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    scroll.setWidget(content)
    QScroller.grabGesture(scroll.viewport(), QScroller.ScrollerGestureType.TouchGesture)
    return scroll
