"""Top-level window: switches between the visitor screen and the Admin screen."""
from __future__ import annotations

import logging
import time
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QEvent, QLocale, QObject, Qt, QTimer
from PySide6.QtGui import QCloseEvent, QKeyEvent
from PySide6.QtWidgets import QApplication, QDialog, QMainWindow, QStackedWidget

from admin.auth import AdminAuth
from app.command_controller import CommandController
from app.content_controller import ACTOR_ADMIN, ContentController
from robot.command_gateway import CommandType
from robot.interfaces import RobotState
from app.conversation_controller import ConversationController
from app.hardware_controller import HardwareController
from app.mic_array_controller import MicArrayController
from app.mqtt_controller import MqttController
from app.speaker_controller import SpeakerController
from app.robot_controller import SAFETY_STOP_MESSAGE, RobotController
from app.schedule_controller import ScheduleController
from app.speech_controller import SpeechController
from app.state import ConversationState
from app.ai_key import AiKeyController
from app.tuning import TuningController
from app.vision_controller import VisionController
from app.voice_controller import VoiceController
from config.settings import Settings
from database.models import ACTION_FEEDBACK_DOWN, ACTION_FEEDBACK_UP, ACTION_STOP, Schedule
from robot.mock_adapter import MockRobotAdapter
from services.analytics_service import AnalyticsService
from services.conversation_service import ConversationService
from ui.admin_login import AdminLoginDialog
from ui.admin_screen import AdminScreen
from ui.log_bridge import QtLogHandler
from ui.user_screen import UserScreen
from vision.interfaces import SourceStatus
from vision.presence import PresenceEvent
from vision.signs import KIND_GESTURE, Sign

ACTOR_VISION = "vision"
ACTOR_VISITOR = "visitor"
ACTOR_SCHEDULE = "schedule"
LISTEN_AGAIN_MS = 400  # how soon an idle robot opens the microphone again

logger = logging.getLogger(__name__)

STYLE_PATH = Path(__file__).resolve().parent / "styles" / "theme.qss"
_ACTIVITY_EVENTS = frozenset(
    {QEvent.Type.MouseButtonPress, QEvent.Type.KeyPress, QEvent.Type.TouchBegin}
)


def use_arabic_digits() -> None:
    """Show numbers as 0–9 everywhere (spin boxes, number formatting).

    A Windows machine set to Thai can make Qt's system locale use Thai digits
    (๐–๙). Qt's own Thai locale keeps Thai for names but uses Arabic digits.
    Must run before any widget is created; widgets read the default locale then.
    """
    QLocale.setDefault(QLocale(QLocale.Language.Thai, QLocale.Country.Thailand))


def load_style_sheet() -> str:
    """The theme with its icon paths made absolute (QSS resolves url() against the CWD)."""
    return STYLE_PATH.read_text(encoding="utf-8").replace(
        "url(@styles/", f"url({STYLE_PATH.parent.as_posix()}/"
    )


class MainWindow(QMainWindow):
    def __init__(
        self,
        settings: Settings,
        conversation: ConversationController,
        robot: RobotController,
        commands: CommandController,
        auth: AdminAuth,
        content: ContentController,
        voice: VoiceController,
        vision: VisionController,
        speech: SpeechController,
        conversation_service: ConversationService,
        analytics: AnalyticsService,
        schedule: ScheduleController,
        tuning: TuningController,
        ai_key: AiKeyController,
        log_handler: QtLogHandler,
        mock_adapter: MockRobotAdapter | None = None,
        mqtt: MqttController | None = None,
        speaker: SpeakerController | None = None,
        hardware: HardwareController | None = None,
        mic_array: MicArrayController | None = None,
    ) -> None:
        use_arabic_digits()  # before the window exists: child widgets take the window's locale
        super().__init__()
        self._settings = settings
        self._auth = auth
        self._content = content
        self._conversation = conversation
        self._vision = vision
        self._analytics = analytics
        self._robot = robot
        self._tuning = tuning
        # What the current visitor (as seen by the camera) has done so far.
        self._visit_engaged = False
        self._visit_questions = 0
        self._visit_greeted = False  # the camera's visitor has had their greeting (or did not need one)
        self._last_greeting = float("-inf")  # time.monotonic() of the last spoken greeting
        self._speech = speech
        self._voice = voice
        self._login_dialog: AdminLoginDialog | None = None

        self.setWindowTitle(f"AI Reception Robot — {settings.institution_name}")
        self.setStyleSheet(load_style_sheet())

        self.user_screen = UserScreen(
            settings, conversation, robot, commands, content, voice, vision,
            conversation_service, ai_key, hardware=hardware, mic_array=mic_array,
        )
        self.admin_screen = AdminScreen(
            settings, robot, auth, content, voice, vision, speech, conversation_service,
            analytics, tuning, ai_key, log_handler, mock_adapter, mqtt=mqtt, speaker=speaker,
            hardware=hardware,
        )
        robot.safety_stopped.connect(self._on_safety_stop)
        self._stack = QStackedWidget()
        self._stack.addWidget(self.user_screen)
        self._stack.addWidget(self.admin_screen)
        self.setCentralWidget(self._stack)

        self.user_screen.admin_requested.connect(self.open_admin)
        self.admin_screen.logout_requested.connect(self.leave_admin)
        self.admin_screen.quit_requested.connect(self.close)
        self.admin_screen.show_on_showcase.connect(self._show_on_showcase)
        conversation.turn_finished.connect(self._on_turn_finished)
        vision.visitor_arrived.connect(self._on_visitor_arrived)
        vision.visitor_attentive.connect(self._on_visitor_attentive)
        vision.visitor_engaged.connect(self._on_visitor_engaged)
        vision.sign_shown.connect(self._on_sign_shown)
        self.user_screen.feedback_given.connect(self._record_feedback)
        conversation.answer_ready.connect(self._count_question)
        self.admin_screen.run_schedule_requested.connect(self._run_schedule_now)
        schedule.perform = self._perform_schedule
        vision.visitor_left.connect(self._on_visitor_left)

        # Any touch/key anywhere in the app counts as activity.
        self._idle_timer = QTimer(self)
        self._idle_timer.setSingleShot(True)
        self._idle_timer.setInterval(settings.idle_timeout_s * 1000)
        self._idle_timer.timeout.connect(self._on_idle)
        self._idle_timer.start()
        QApplication.instance().installEventFilter(self)

        # Always listening: the visitor never has to press "start" for each question.
        self._listen_timer = QTimer(self)
        self._listen_timer.setInterval(LISTEN_AGAIN_MS)
        self._listen_timer.timeout.connect(self._keep_listening)
        self._listen_timer.start()

    def _keep_listening(self) -> None:
        """The robot is idle: be ready for the next question without a button press.

        "หยุดสนทนา" pauses this until "เริ่มสนทนา". With a working camera it waits for
        someone at conversation distance, so an empty room is not answered.
        """
        tuning = self._tuning.values
        if not (tuning.voice_follow_up and tuning.vision_auto_listen) or not self._visitor_screen_is_free():
            return  # the Admin turned automatic listening off
        if not self.user_screen.can_continue_conversation():
            return
        self.user_screen.keep_listening()  # always: whether or not the camera sees anyone, sound that comes in is heard

    def in_admin_mode(self) -> bool:
        return self._stack.currentWidget() is self.admin_screen

    def closeEvent(self, event: QCloseEvent) -> None:
        # Silent from the moment the window goes, not when shutdown gets to it.
        self._conversation.end_session()
        super().closeEvent(event)

    def open_admin(self) -> None:
        self._conversation.end_session()
        self._login_dialog = AdminLoginDialog(self._auth, self)
        accepted = self._login_dialog.exec() == QDialog.DialogCode.Accepted
        self._login_dialog = None
        if accepted:
            self.enter_admin()

    def enter_admin(self) -> None:
        logger.info("Entered admin mode")
        self._content.log_activity(ACTOR_ADMIN, "admin.login")
        self.user_screen.suspend()
        self._stack.setCurrentWidget(self.admin_screen)
        if self._settings.admin_idle_timeout_s:
            self._idle_timer.setInterval(self._settings.admin_idle_timeout_s * 1000)
        self._idle_timer.start()

    def leave_admin(self) -> None:
        if not self.in_admin_mode():
            return
        logger.info("Left admin mode")
        self._content.log_activity(ACTOR_ADMIN, "admin.logout")
        self._conversation.end_session()  # also ends a speech preview started in the Admin
        self.admin_screen.reset()
        self._stack.setCurrentWidget(self.user_screen)
        self.user_screen.show_page(self.user_screen.current_page())
        self._idle_timer.setInterval(self._settings.idle_timeout_s * 1000)
        self._idle_timer.start()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if event.type() in _ACTIVITY_EVENTS:
            self._idle_timer.start()
        return False

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_F11:
            if self.isFullScreen():
                self.showMaximized()
            else:
                self.showFullScreen()
            return
        super().keyPressEvent(event)

    def _show_on_showcase(self, media_id: int) -> None:
        self.leave_admin()
        self.user_screen.play_on_home(media_id)

    def _on_turn_finished(self, answered: bool) -> None:
        """The robot has finished its turn: listen without a button press, if appropriate.

        After an answer this is follow-up listening, within a session the visitor
        started; with nothing to listen for, that session is over. After a greeting
        it is the hands-free start for a visitor who stepped up in the meantime.
        """
        if answered and not self._conversation.session_active:
            return  # typed, or heard without "start": the microphone does not open by itself
        if self._should_listen_again(answered):
            if answered:
                self.user_screen.listen_for_follow_up()
            else:
                self.user_screen.listen_hands_free(quiet=True)  # after a greeting: a new conversation
            self._idle_timer.start()
        elif answered:
            self._conversation.end_session()

    def _should_listen_again(self, answered: bool) -> bool:
        tuning = self._tuning.values
        allowed = tuning.voice_follow_up if answered else tuning.vision_auto_listen
        if not allowed or not self._visitor_screen_is_free():
            return False
        if not answered and self._vision.status is not SourceStatus.RUNNING:
            return False  # a greeting only leads to listening when the camera says someone is close
        if not self.user_screen.can_continue_conversation():
            return False
        # Listen again whether or not the camera sees anyone (9 Nov): the conversation goes on, sound that comes in is
        # heard and answered, and only "หยุดสนทนา" ends it. (Silence does not end it either: see VoiceController.)
        return True

    def _visitor_screen_is_free(self) -> bool:
        """False while the Admin (or a dialog) owns the screen; the camera must not disturb it."""
        return not self.in_admin_mode() and QApplication.activeModalWidget() is None

    def _on_visitor_arrived(self, event: PresenceEvent) -> None:
        """Someone has come into the service area: noted, but not greeted yet (see ``_on_visitor_attentive``)."""
        distance = "?" if event.distance_m is None else f"{event.distance_m:.1f}"
        self._content.log_activity(ACTOR_VISION, "visitor.arrived", f"distance={distance}m")
        self._visit_engaged = False
        self._visit_questions = 0
        self._visit_greeted = False

    def _on_visitor_attentive(self, event: PresenceEvent) -> None:
        """The visitor has come into range (or, if VISION_GREET_STILL_SECONDS is set, stopped in front of the camera): greet them, once.

        Once per visit, and not at all while a conversation is going on or the screen is the Admin's. Nobody is
        greeted before the camera is running: the robot does not greet at start-up or to an empty room.
        """
        self._visit_greeted = True  # from here the visitor may be listened to hands-free
        if not self._visitor_screen_is_free():
            logger.info("Greeting skipped: the screen is in use by the Admin")
            return
        state = self._conversation.state.state
        waiting = state is ConversationState.LISTENING and not self._voice.heard_speech
        if state is not ConversationState.IDLE and not waiting:
            # Answering, or hearing someone speak: never cut in. (A robot that only sits listening, with nobody
            # having said anything yet, is not in a conversation: the visitor who arrives is greeted.)
            logger.info("Greeting skipped: a conversation is already going on (%s)", state.value)
            return
        since_greeting = time.monotonic() - self._last_greeting
        if since_greeting < self._settings.vision_greet_cooldown_s:
            # Most likely the same person, lost by the camera for a moment.
            logger.info(
                "Greeting skipped: the last one was %.0f s ago (cooldown %.0f s)",
                since_greeting, self._settings.vision_greet_cooldown_s,
            )
            return
        self._last_greeting = time.monotonic()
        self.user_screen.greet(self._content.greeting_at(datetime.now()))
        # A gesture to go with the words, if one is configured (none while the
        # robot has no arms). It passes the same checks as any
        # command, so it is simply skipped if the visitor is already within reach.
        gesture = self._settings.greet_action
        if gesture and self._robot.status.state is RobotState.IDLE:
            self._robot.send(CommandType.ACTION.value, gesture, source="auto", quiet=True)
        self._idle_timer.start()

    def _on_safety_stop(self, _reason: str) -> None:
        self.user_screen.notify(SAFETY_STOP_MESSAGE, "error")

    def _on_visitor_engaged(self, _event: PresenceEvent) -> None:
        """They have stepped up to the robot: start listening, hands-free."""
        self._content.log_activity(ACTOR_VISION, "visitor.engaged")
        self._visit_engaged = True
        if self._settings.vision_greet_still_s > 0 and not self._visit_greeted:
            return  # greeted first: the mic opens by itself once the greeting is over (``_keep_listening``)
        if not self._tuning.values.vision_auto_listen or not self._visitor_screen_is_free():
            return
        if self._conversation.state.state is not ConversationState.IDLE:
            return  # already listening, thinking or answering
        if not self.user_screen.can_continue_conversation():
            return  # they went back to the home page by hand; do not pull them away
        self.user_screen.listen_hands_free()
        self._idle_timer.start()

    def _on_sign_shown(self, sign: Sign) -> None:
        """A card or a hand gesture was held up to the camera."""
        if not self._visitor_screen_is_free():
            return
        trigger = self._content.find_trigger(sign.kind, sign.code)
        if trigger is None:
            return  # the Admin has not given this card/gesture a meaning
        if sign.kind == KIND_GESTURE and not self._vision.present:
            return  # hands of people walking past are not talking to the robot
        self._content.log_activity(
            ACTOR_VISION, "sign.shown", f"{sign.kind} {sign.code} {trigger.title}"
        )
        self._idle_timer.start()

        if trigger.action == ACTION_STOP:
            self._conversation.end_session()
            self.user_screen.notify("หยุดแล้วครับ")
            if sign.kind == KIND_GESTURE:
                self.user_screen.react_to_gesture(sign.code, trigger.title, "", speak=False)
            return
        # The robot's own answer is not cut off by a thumbs up: the face alone replies then.
        busy = self._conversation.answering
        if trigger.action in (ACTION_FEEDBACK_UP, ACTION_FEEDBACK_DOWN):
            # Rates the latest answer if there is one; a thumb is answered either way.
            self._record_feedback(trigger.action == ACTION_FEEDBACK_UP)
            if sign.kind == KIND_GESTURE:
                self.user_screen.react_to_gesture(sign.code, trigger.title, trigger.response_text, not busy)
            elif trigger.response_text and not busy:
                self._conversation.say(trigger.response_text)
        elif sign.kind == KIND_GESTURE and trigger.media_id is None:
            self.user_screen.react_to_gesture(sign.code, trigger.title, trigger.response_text, not busy)
        elif trigger.media_id is not None:
            # Next on the home page, without leaving the page that is open; the text, if any, is spoken.
            self.user_screen.queue_media(trigger.media_id)
            if trigger.response_text:
                self._conversation.say(trigger.response_text)
        else:
            self.user_screen.show_sign_response(trigger.title, trigger.response_text)

    def _record_feedback(self, positive: bool) -> bool:
        """Store the visitor's opinion of the latest answer. False if there is nothing to rate."""
        unrated = self._conversation.take_unrated_answer()
        if unrated is None:
            return False
        answer, interaction_id = unrated
        if interaction_id is not None:
            self._analytics.record_feedback(interaction_id, positive)
        self._content.log_activity(
            ACTOR_VISITOR,
            "feedback.up" if positive else "feedback.down",
            f"source={answer.source} language={answer.language}",
        )
        self.user_screen.feedback_recorded(positive)
        return True

    def _on_visitor_left(self, event: PresenceEvent) -> None:
        closest = "?" if event.closest_m is None else f"{event.closest_m:.1f}"
        self._content.log_activity(
            ACTOR_VISION, "visitor.left", f"duration={event.duration_s:.0f}s closest={closest}m"
        )
        self._analytics.record_visit(
            event.duration_s, event.closest_m, self._visit_engaged, self._visit_questions
        )
        # The camera not seeing anyone any more does NOT end the conversation (the team's rule, 9 Nov): a sound that
        # comes in is still heard and answered, and what is being said is not cut off. Only "หยุดสนทนา" ends it.

    def _count_question(self, _result: object) -> None:
        if self._vision.present:
            self._visit_questions += 1

    def _perform_schedule(self, schedule: Schedule) -> bool:
        """Deliver a scheduled announcement if the robot is free. False = try again shortly."""
        busy = (
            not self._visitor_screen_is_free()
            or self._conversation.state.state is not ConversationState.IDLE
            or self._vision.engaged
        )
        if busy:
            return False
        self._announce(schedule)
        return True

    def _run_schedule_now(self, schedule: Schedule) -> None:
        """The Admin pressed "announce now": show it regardless of the clock."""
        self.leave_admin()
        self._announce(schedule)

    def _announce(self, schedule: Schedule) -> None:
        self._content.log_activity(ACTOR_SCHEDULE, "schedule.run", f"id={schedule.id} {schedule.title}")
        self.user_screen.announce(schedule.message, schedule.media_id)
        self._idle_timer.start()

    def _on_idle(self) -> None:
        if self.in_admin_mode() and not self._settings.admin_idle_timeout_s:
            self._idle_timer.start()  # ADMIN_IDLE_TIMEOUT_S=0: the Admin stays until logging out
            return
        if self._login_dialog is not None:
            self._login_dialog.reject()
        # Leave an open Admin dialog (edit form, preview) before logging out.
        modal = QApplication.activeModalWidget()
        if isinstance(modal, QDialog):
            modal.reject()
        # Admin times out (when ADMIN_IDLE_TIMEOUT_S is set), even with someone standing there. The visitor
        # screen is left alone while the camera still sees a visitor reading it; otherwise the last
        # visitor's conversation is cleared, but the page stays as it is.
        self.leave_admin()
        if not self._vision.present and self.user_screen.listening_paused:
            self.user_screen.clear_session()  # the visitor pressed "หยุดสนทนา" and has not come back: clear the screen
        self._idle_timer.start()

    # --- the robot never greets on its own: only a visitor who comes into the camera's range is greeted, once ---

    def watch_camera_for_greeting(self) -> None:
        """Kept for the application's start-up call. There is no start-up greeting any more: the robot waits
        for someone to stop in front of the camera (``_on_visitor_attentive``), and with no camera it waits
        for someone to use the screen."""
