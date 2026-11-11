"""Composition root: builds the services and wires them to the UI."""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Callable
from pathlib import Path

from PySide6.QtCore import QThreadPool

from admin.auth import AdminAuth, migrate_legacy_credentials
from app.ai_key import AiKeyController
from app import __version__
from app.content_controller import ContentController
from app.conversation_controller import ConversationController
from app.robot_controller import RobotController
from app.schedule_controller import ScheduleController
from ai import knowledge_service
from ai.gemini_client import GeminiClient
from app.speech_controller import SpeechController, SpeechStatus
from audio.cues import CueLibrary
from services.expression import ExpressionPolicy
from app.state import StateManager
from app.workers import FunctionWorker
from audio.text_to_speech import TextToSpeechService, TtsConfig
from services.audio_playback_service import AudioPlaybackService
from services.conversation_service import ConversationResult
from app.vision_controller import VisionController
from app.voice_controller import VoiceController
from app.tuning import Tuning, TuningController
from audio.microphone import MicrophoneService
from audio.speech_to_text import SpeechToTextService, SttConfig
from app.command_controller import CommandController
from app.hardware_controller import GROUP_MOVE, GROUP_TURN, HardwareController, hardware_config
from app.mqtt_controller import MqttController, mqtt_config
from app.speaker_controller import SpeakerController
from audio.direction import DirectionSource, UnavailableDirectionSource
from audio.mic_array import find_device as find_mic_array
from audio.voice_activity import VoiceActivityDetector
from audio.xvf3800 import Xvf3800DirectionSource, usb_available
from app.direction_controller import DirectionController
from app.mic_array_controller import MicArrayController
from services.speaker_matching import MatchConfig, MountGeometry
from config.settings import PART_BASE, ConfigError, Settings
from robot.safety import ACTION, Proximity, ProximityInterlock
from database.db import Database
from database.repositories import (
    AdminUserRepository,
    InteractionRepository,
    MediaRepository,
    ScheduleRepository,
    SettingsRepository,
    VisitRepository,
)
from services.analytics_service import AnalyticsService
from robot.command_gateway import CommandGateway
from robot.interfaces import RobotAdapter, RobotError
from robot.mock_adapter import MockRobotAdapter
from services.conversation_service import ConversationService
from services.conversation_service import LANG_ENGLISH, LANG_THAI
from database.models import ACTION_SAY
from services.media_service import MediaService
from services.hardware_link import HardwareLink
from services.mqtt_service import MqttService
from ui.log_bridge import QtLogHandler
from ui.main_window import MainWindow
from vision.interfaces import VisionSource
from vision.oak_source import OakConfig, OakVisionSource

logger = logging.getLogger(__name__)

SHUTDOWN_WAIT_MS = 3000
DATABASE_FILE = "app.db"
LEGACY_CREDENTIALS_FILE = "admin_credentials.json"

ACTOR_VISITOR = "visitor"


def build_robot_adapter(settings: Settings) -> RobotAdapter:
    if settings.robot_adapter == "mock":
        return MockRobotAdapter()
    raise ConfigError(f"ROBOT_ADAPTER='{settings.robot_adapter}' ยังไม่รองรับในรุ่นนี้")


def build_direction_source(settings: Settings) -> DirectionSource:
    """The microphone that measures voice direction. None is supported by real hardware yet."""
    if settings.audio_direction_source == "xvf3800":
        # Imported here, on the main thread, before any worker starts (a cross-thread import deadlocked once).
        ok, reason = usb_available()
        if not ok:
            return UnavailableDirectionSource(f"ตั้งค่าเป็น ReSpeaker XVF3800 แต่ใช้ไม่ได้: {reason}")
        return Xvf3800DirectionSource()
    return UnavailableDirectionSource(
        "ไม่มีไมโครโฟนที่วัดทิศทางเสียงได้ (ไมค์ในโน้ตบุ๊กไม่ถือว่ารองรับ) "
        "ต้องใช้ไมโครโฟนอาเรย์ที่ให้ค่า DOA เช่น ReSpeaker XVF3800"
    )


class Application:
    def __init__(
        self,
        settings: Settings,
        log_handler: QtLogHandler,
        microphone: MicrophoneService | None = None,
        stt: SpeechToTextService | None = None,
        vision_source: VisionSource | None = None,
        tts: TextToSpeechService | None = None,
        playback: AudioPlaybackService | None = None,
        gemini: GeminiClient | None = None,
        env_path: Path | None = None,
        direction_source: DirectionSource | None = None,
        mic_array_detector: Callable[[], str | None] | None = None,
    ) -> None:
        """``env_path``: the .env file the Admin may write a new API key to (None = not allowed)."""
        # The device- and network-facing parts can be replaced in tests; the
        # app uses the real ones.
        self.settings = settings
        self.finished_cleanly = True
        for directory in (settings.data_dir, settings.media_dir):
            directory.mkdir(parents=True, exist_ok=True)

        self.pool = QThreadPool()
        self.database = Database(settings.data_dir / DATABASE_FILE)
        self.database.initialize()

        self.media_service = MediaService(
            MediaRepository(self.database),
            settings.media_dir,
            settings.max_video_bytes,
            settings.max_image_bytes,
        )
        self.content = ContentController(settings, self.database, self.media_service, self.pool)
        self.content.start_in_default_venue()  # always begins with the invention contest
        # Speech speed, listening and camera distances: .env values the Admin may override.
        self.tuning = TuningController(
            settings, SettingsRepository(self.database), audit=self.content.log_activity
        )
        tuned = self.tuning.values

        credentials = AdminUserRepository(self.database)
        migrate_legacy_credentials(settings.data_dir / LEGACY_CREDENTIALS_FILE, credentials)
        self.auth = AdminAuth(credentials)

        self.adapter = build_robot_adapter(settings)
        try:
            self.adapter.connect()
        except RobotError:
            # The app must still open (announcements, Admin) without a robot.
            logger.exception("Robot connection failed at startup")

        # With a camera, the robot does not move while it sees someone within
        # reach. With the camera switched off there is nothing to check with.
        self.vision: VisionController | None = None
        self.interlock: ProximityInterlock | None = None
        if vision_source is not None or settings.vision_enabled:
            self.interlock = ProximityInterlock(
                self._proximity,
                base_clearance_m=settings.base_clearance_m,
                base_actions=[a.id for a in settings.actions if a.part == PART_BASE],
            )
        self.gateway = CommandGateway(
            self.adapter,
            allowed_actions=[action.id for action in settings.actions],
            timeout_s=settings.command_timeout_s,
            interlock=self.interlock.check if self.interlock else None,
        )
        self.robot = RobotController(
            self.gateway, self.adapter, self.pool, audit=self.content.log_activity,
            interlock=self.interlock, simulated=isinstance(self.adapter, MockRobotAdapter),
        )
        self.commands = CommandController(
            settings.actions, self.robot, self.interlock,
            understands_speech=settings.voice_commands,
        )

        self.speech = SpeechController(
            enabled=settings.tts_enabled,
            tts=tts or TextToSpeechService(TtsConfig(
                speed=tuned.tts_speed, engine=settings.tts_engine,
                thai_voice=self.content.tts_voices(settings.tts_voice_th, settings.tts_voice_en)[0],
                english_voice=self.content.tts_voices(settings.tts_voice_th, settings.tts_voice_en)[1],
                cache_dir=str(settings.data_dir / "voice_cache"), cache_megabytes=settings.tts_cache_mb,
            )),
            playback=playback or AudioPlaybackService(),
            output_device=self.content.audio_output_name,
            pool=self.pool,
            cues=CueLibrary(settings.media_dir / "voice_cues"),
        )

        # The lines the robot is sure to say (greetings, what a gesture or card answers with) are made in
        # the background once the speech is ready, so they play at once and need no network.
        self._lines_prepared = False
        self.speech.status_changed.connect(self._prepare_known_lines)

        if gemini is None and settings.ai_enabled and settings.gemini_api_key:
            gemini = GeminiClient(
                settings.gemini_api_key, settings.gemini_model, settings.gemini_timeout_s
            )
            # Loading the SDK and creating the client take ~0.6 s; do it now, not on the first question.
            self.pool.start(FunctionWorker(None, gemini.warm_up))
        self.conversation_service = ConversationService(
            # Only the kind of place in use (and shared entries): never another place's facts.
            knowledge_provider=self.content.active_knowledge,
            institution_name=settings.institution_name,
            gemini=gemini,
            announcements_provider=self._announcement_texts,
            persona_provider=self.content.persona,
            venue_provider=lambda: (self.content.venue(), self.content.place_name()),
            # Per question: the place in use, or the place the question is clearly about.
            router=self.content.knowledge_for,
            knowledge_first=settings.ai_knowledge_first,
        )
        self.ai_key = AiKeyController(settings, self.conversation_service, env_path, self.pool)
        self.analytics = AnalyticsService(
            InteractionRepository(self.database),
            VisitRepository(self.database),
            log_questions=settings.log_questions,
            retention_days=settings.usage_retention_days,
        )
        self.analytics.purge_old()
        self.schedule = ScheduleController(ScheduleRepository(self.database))
        self.conversation = ConversationController(
            self.conversation_service,
            StateManager(),
            self.pool,
            speech=self.speech,
            on_answer=self._record_answer,
            commands=self.commands.handle_text,
            expression=ExpressionPolicy(),
        )
        self.voice = VoiceController(
            enabled=settings.stt_enabled,
            microphone=microphone or MicrophoneService(),
            stt=stt or SpeechToTextService(
                SttConfig(
                    model=settings.stt_model,
                    device=settings.stt_device,
                    compute_type=settings.stt_compute_type,
                    language=settings.stt_language,
                    model_dir=settings.model_dir,
                    thai_model=settings.stt_thai_model,
                    thai_model_revision=settings.stt_thai_model_revision,
                    detector_model=settings.stt_detector_model,
                )
            ),
            conversation=self.conversation,
            endpoint_config=tuned.endpoint_config(),
            device_name=self.content.mic_device_name,
            pool=self.pool,
            channel=self.content.mic_channel,
        )
        # Work out the knowledge base's comparison texts now, in the background: the first question
        # would otherwise spend about 0.15 s on it, the ones after that almost nothing.
        self.pool.start(FunctionWorker(None, self._warm_knowledge))
        # Tells a voice from noise, so a fan or a click never starts a turn or holds one open. Loaded here, on the
        # main thread, before any worker imports faster-whisper.
        if settings.mic_voice_filter and settings.stt_enabled and microphone is None:  # (a stand-in microphone has no real sound)
            detector = VoiceActivityDetector()
            if detector.load():
                self.voice.voice_filter = detector
                logger.info("Voice filter on: loud sounds that are not a voice do not count as speech")
        # Listens to ONE voice: the visitor who started the turn; other people's voices are not answered.
        if settings.mic_speaker_lock and settings.stt_enabled and microphone is None:
            self.voice.prepare_speaker_lock(settings.model_dir)
        if vision_source is None and settings.vision_enabled:
            vision_source = OakVisionSource(
                OakConfig(
                    model=settings.vision_model,
                    fps=settings.vision_fps,
                    model_dir=settings.model_dir / "depthai",
                    preview=settings.vision_preview,
                    preview_fps=settings.vision_preview_fps,
                    signs=settings.vision_signs,
                    gestures=settings.vision_gestures,
                    sign_model_dir=settings.model_dir / "mediapipe",
                )
            )
        self.vision = VisionController(
            vision_source, area_config=tuned.area_config(), talk_config=tuned.talk_config(),
            attention_still_s=settings.vision_greet_still_s,
        )
        self.vision.visitor_arrived.connect(self.voice.reset_speaker)  # a new visitor: their voice is the one to follow
        self.vision.visitor_left.connect(self.voice.reset_speaker)
        self.tuning.changed.connect(self._apply_tuning)
        # Voice direction and speaker matching: informs, never moves the robot.
        source = direction_source or build_direction_source(settings)
        # A board that can be calibrated: the direction controller loads its saved calibration
        # before the source starts, so the first readings already use it.
        self.direction: DirectionController | None = None
        if isinstance(source, Xvf3800DirectionSource):
            self.direction = DirectionController(
                source, SettingsRepository(self.database), self.voice, self.vision,
                MountGeometry(
                    mic_x_m=settings.mic_offset_x_m, mic_z_m=settings.mic_offset_z_m,
                    mic_yaw_deg=0.0, camera_hfov_deg=settings.camera_hfov_deg,
                ),
                is_robot_speaking=lambda: self.speech.is_speaking,
            )
        self.speaker = SpeakerController(
            source,
            self.vision,
            self.voice,
            self.speech,
            MountGeometry(
                mic_x_m=settings.mic_offset_x_m,
                mic_z_m=settings.mic_offset_z_m,
                mic_yaw_deg=settings.mic_yaw_deg,
                camera_hfov_deg=settings.camera_hfov_deg,
            ),
            MatchConfig(tolerance_deg=settings.speaker_match_tolerance_deg),
        )
        if self.direction is not None:
            self.speaker.board = self.direction
            self.speaker.veto_enabled = self.direction.utterance_veto_enabled
            self.voice.direction_gate = self.direction.blocks_speech
            self.voice.turn_started = self.direction.begin_turn
        # MQTT communication test link: its own service and status, never the robot's.
        self.mqtt: MqttController | None = None
        if settings.mqtt_enabled:
            self.mqtt = MqttController(MqttService(mqtt_config(settings)), self.pool)
        # The real hardware's MQTT link: Admin direction commands only, off while MQTT_HW_HOST is empty.
        self.hardware: HardwareController | None = None
        if settings.hw_mqtt_host:
            camera_rule = ProximityInterlock(self._proximity, settings.base_clearance_m, ["direction"])
            self.hardware = HardwareController(
                HardwareLink(hardware_config(settings)),
                audit=self.content.log_activity,
                safety_check=(lambda: camera_rule.check(ACTION, "direction")) if settings.hw_require_camera else None,
                topics=settings.hw_topics,
                store=SettingsRepository(self.database),
                default_topics={GROUP_TURN: settings.hw_turn_topic, GROUP_MOVE: settings.hw_move_topic},
            )
        # Whether the microphone array is plugged in; None = the screen does not ask (MIC_ARRAY_REQUIRED=false).
        self.mic_array = (
            MicArrayController(mic_array_detector or find_mic_array, self.pool)
            if settings.mic_array_required else None
        )
        self.window = MainWindow(
            settings,
            self.conversation,
            self.robot,
            self.commands,
            self.auth,
            self.content,
            self.voice,
            self.vision,
            self.speech,
            self.conversation_service,
            self.analytics,
            self.schedule,
            self.tuning,
            self.ai_key,
            log_handler,
            mock_adapter=self.adapter if isinstance(self.adapter, MockRobotAdapter) else None,
            mqtt=self.mqtt,
            speaker=self.speaker,
            hardware=self.hardware,
            mic_array=self.mic_array,
        )
        self.speech.cue_started.connect(self.window.user_screen.on_cue)  # the face laughs along with the sound
        if self.mic_array is not None:
            self.mic_array.start()
        if self.mqtt is not None:
            self.mqtt.start()  # connects in the background; the dashboard shows how it goes
        if self.hardware is not None:
            self.hardware.start()  # connects only; nothing is sent until the Admin presses a button
        logger.info(
            "Application started: version=%s adapter=%s demo_mode=%s",
            __version__, settings.robot_adapter, settings.demo_mode,
        )

    def _apply_tuning(self, tuning: Tuning) -> None:
        """The Admin saved new values: use them from now on, without a restart."""
        self.speech.set_speed(tuning.tts_speed)
        self.voice.set_endpoint_config(tuning.endpoint_config())
        self.vision.set_presence_configs(tuning.area_config(), tuning.talk_config())

    def _proximity(self) -> Proximity:
        """What the camera knows about people near the robot (read from any thread)."""
        vision = self.vision
        if vision is None:
            return Proximity(reliable=False)
        return Proximity(
            reliable=vision.fresh, nearest_m=vision.nearest_m, unmeasured=vision.unmeasured_nearby
        )

    def _announcement_texts(self) -> list[str]:
        """Current announcements of the kind of place in use, as part of what the AI may answer from."""
        return [
            f"{item.title} {item.body}".strip()
            for item in self.content.active_announcements()
        ]

    def _record_answer(
        self, question: str, result: ConversationResult, response_ms: int, mode: str
    ) -> int | None:
        """Store one answered question for the statistics; returns its record id."""
        detail = f"source={result.source} language={result.language} mode={mode}"
        if result.ai_error:
            detail += f" ai_error={result.ai_error}"
        self.content.log_activity(ACTOR_VISITOR, "conversation.answer", detail)
        try:
            return self.analytics.record_interaction(question, result, response_ms, mode)
        except Exception:
            # Statistics must never get in the way of answering.
            logger.exception("Could not store the interaction")
            return None

    def show(self) -> None:
        if self.settings.fullscreen:
            self.window.showFullScreen()
        else:
            # Maximized rather than a fixed size, so the window always fits the screen.
            self.window.showMaximized()
        self.window.watch_camera_for_greeting()  # no start-up greeting: only a visitor who stops in front of the camera is greeted

    def _prepare_known_lines(self) -> None:
        if self._lines_prepared or self.speech.status is not SpeechStatus.READY:
            return
        self._lines_prepared = True
        self.speech.prerender(self._known_lines())

    def _known_lines(self) -> list[str]:
        """What the robot says without being asked a question, in the order it is most likely needed."""
        lines: list[str] = []
        for trigger in self.content.triggers():
            if trigger.enabled and trigger.action == ACTION_SAY and trigger.response_text:
                lines.append(trigger.response_text)
        for language in (LANG_THAI, LANG_ENGLISH):
            for hour in (9, 14, 19):  # the greetings of the morning, the afternoon and the evening
                lines.append(self.content.greeting_at(datetime(2026, 1, 1, hour), language))
        return list(dict.fromkeys(line.strip() for line in lines if line.strip()))

    def _warm_knowledge(self) -> None:
        try:
            items = self.content.knowledge()
            knowledge_service.rank("ขอสอบถามข้อมูลหน่อยครับ", items)
        except Exception:  # noqa: BLE001 - only a head start
            logger.debug("Knowledge warm-up skipped", exc_info=True)

    def shutdown(self) -> None:
        """Stop everything. ``finished_cleanly`` is False if a background task is still running."""
        logger.info("Application shutting down")
        self.conversation.end_session()  # also silences speech
        self.commands.cancel()
        self.schedule.shutdown()
        self.voice.shutdown()
        self.speech.close()  # ends the check on a voice that failed
        if self.direction is not None:
            self.direction.shutdown()
        self.speaker.shutdown()
        if self.mic_array is not None:
            self.mic_array.shutdown()
        self.vision.shutdown()
        self.window.user_screen.showcase.deactivate()
        self.robot.shutdown()
        self.adapter.disconnect()
        if self.mqtt is not None:
            self.mqtt.shutdown()
        if self.hardware is not None:
            self.hardware.shutdown()
        self.finished_cleanly = self.pool.waitForDone(SHUTDOWN_WAIT_MS)
        if not self.finished_cleanly:
            logger.warning("Background tasks did not finish within %d ms", SHUTDOWN_WAIT_MS)
