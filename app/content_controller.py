"""Everything the screens need to read or change stored content.

Admin edits go through here so that each change is validated, recorded in the
activity log, and announced with ``content_changed`` for the visitor screen.
"""
from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass, fields, replace
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QObject, QThreadPool, Signal, Slot

from app.workers import FunctionWorker
from config.settings import Settings
from database.db import Database
from database.models import (
    ACTION_FEEDBACK_DOWN,
    ACTION_FEEDBACK_UP,
    ACTION_SAY,
    ACTION_STOP,
    TRIGGER_CARD,
    TRIGGER_GESTURE,
    ActivityLog,
    Announcement,
    KnowledgeItem,
    MediaItem,
    Schedule,
    VisualTrigger,
)
from database.repositories import (
    ActivityLogRepository,
    AnnouncementRepository,
    KnowledgeRepository,
    ScheduleRepository,
    SettingsRepository,
    VisualTriggerRepository,
)
from services import knowledge_transfer, venue_router, venues
from services.knowledge_transfer import parse_date
from services.venue_router import Place, RoutedKnowledge
from services.media_service import MediaError, MediaService
from services.conversation_service import ENGLISH_GREETING_REPLY, LANG_ENGLISH, LANG_THAI
from services.persona import (
    ANSWER_LENGTHS,
    DEFAULT_GREETING,
    MAX_NAME_CHARS,
    MAX_PHRASE_CHARS,
    MAX_ROLE_CHARS,
    Persona,
)
from services.schedule_service import normalize_time, normalize_weekdays
from vision.aruco_4x4_50 import MARKER_COUNT
from vision.signs import GESTURE_LABELS

logger = logging.getLogger(__name__)

KEY_SEEDED = "content_seeded"
KEY_WELCOME = "welcome_message"
KEY_IMAGE_SECONDS = "image_display_seconds"
KEY_ANNOUNCEMENT_SECONDS = "announcement_interval_seconds"
KEY_MIC_DEVICE = "mic_device_name"
KEY_MIC_CHANNEL = "mic_channel"
KEY_AUDIO_OUTPUT = "audio_output_name"
MAX_MIC_CHANNEL = 7
KEY_GREETING = "greeting_message"
KEY_HOME_DISPLAY = "home_display"
KEY_PRIVACY_ENABLED = "privacy_notice_enabled"
KEY_PRIVACY_TEXT = "privacy_notice_text"
MAX_PRIVACY_CHARS = 250
KEY_VENUE = "venue_profile"  # the kind of place in use (services/venues.py)
KEY_VENUE_NAME = "venue_name."  # + venue id: the place's own name, e.g. "โรงพยาบาลสมุทรปราการ"
# "1" (default): a question that sounds like another kind of place is answered from
# that place's data (services/venue_router.py); "0": only the place in use.
KEY_VENUE_AUTO = "venue_auto"
MAX_PLACE_NAME_CHARS = 120
HOME_MEDIA = "media"  # the playlist and announcements, camera small at the side
HOME_CAMERA = "camera"  # the live camera, large
HOME_DISPLAY_CHOICES = (HOME_MEDIA, HOME_CAMERA)
DEFAULT_IMAGE_SECONDS = 8
DEFAULT_ANNOUNCEMENT_SECONDS = 8
MIN_SECONDS, MAX_SECONDS = 3, 300

KEY_TRIGGERS_SEEDED = "triggers_seeded"
KEY_GESTURES_SEEDED = "gestures_seeded_2"
KEY_INTRO_GESTURE_SEEDED = "intro_gesture_seeded_1"
KEY_SIX_SEVEN_SEEDED = "six_seven_gesture_seeded_1"
KEY_WAVES_SEEDED = "wave_gestures_seeded_1"
INTRO_SPEECH = (
    'สวัสดีครับ ท่านคณะกรรมการทุกท่าน\n'
    'ผม AIRA หุ่นยนต์ประชาสัมพันธ์อัจฉริยะ ยินดีต้อนรับทุกท่านเข้าสู่การนำเสนอในวันนี้ครับ\n'
    'ผมมีหน้าที่ช่วยประชาสัมพันธ์ ให้ข้อมูล และอำนวยความสะดวกแก่ผู้คน โดยผมสามารถรับรู้และตอบสนองต่อสิ่งต่าง ๆ รอบตัวได้\n'
    'หวังว่าทุกท่านจะสนุกกับการรับชมความสามารถของผมนะครับ ขอบคุณครับ'
)
CARD_COUNT = MARKER_COUNT  # cards are numbered 0 .. CARD_COUNT - 1
TRIGGER_ACTIONS = (ACTION_SAY, ACTION_FEEDBACK_UP, ACTION_FEEDBACK_DOWN, ACTION_STOP)

ACTOR_ADMIN = "admin"
ACTOR_SYSTEM = "system"

# Where each persona value is stored. The general greeting keeps its older key.
_PERSONA_KEYS = {
    field.name: KEY_GREETING if field.name == "greeting" else f"persona.{field.name}"
    for field in fields(Persona)
}
# These may not be empty; the others may (an empty farewell means "say nothing").
_PERSONA_REQUIRED = ("robot_name", "fallback", "fallback_en", "greeting")

# Settings that travel in an exported configuration file.
CONFIG_FORMAT = "robot-reception-config"
CONFIG_VERSION = 1
MAX_CONFIG_VALUE_CHARS = 2000
_CONFIG_KEYS = {
    KEY_WELCOME, KEY_IMAGE_SECONDS, KEY_ANNOUNCEMENT_SECONDS, KEY_MIC_DEVICE, KEY_MIC_CHANNEL,
    KEY_AUDIO_OUTPUT, KEY_GREETING, KEY_HOME_DISPLAY, KEY_PRIVACY_ENABLED, KEY_PRIVACY_TEXT,
    KEY_VENUE, KEY_VENUE_AUTO,
}
_CONFIG_PREFIXES = ("persona.", "tuning.", KEY_VENUE_NAME)


def _is_config_key(key: str) -> bool:
    return key in _CONFIG_KEYS or key.startswith(_CONFIG_PREFIXES)


class ContentError(Exception):
    """Invalid input; the message is safe to show to the Admin."""


@dataclass(frozen=True)
class ImportReport:
    added: int
    updated: int
    errors: list[str]  # one message per row that was skipped


class ContentController(QObject):
    content_changed = Signal()
    media_import_finished = Signal(object, str)  # MediaItem | None, error message

    def __init__(
        self,
        settings: Settings,
        database: Database,
        media_service: MediaService,
        pool: QThreadPool,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._settings = settings
        self._database = database
        self._media = media_service
        self._pool = pool
        self._announcements = AnnouncementRepository(database)
        self._knowledge = KnowledgeRepository(database)
        self._app_settings = SettingsRepository(database)
        self._activity = ActivityLogRepository(database)
        self._triggers = VisualTriggerRepository(database)
        self._schedules = ScheduleRepository(database)
        self._seed_if_first_run()
        self._seed_default_triggers()

    # --- activity log ---

    def log_activity(self, actor: str, action: str, detail: str = "") -> None:
        try:
            self._activity.add(actor, action, detail)
        except Exception:
            # Losing an audit row must never break the action being recorded.
            logger.exception("Could not write activity log: %s %s", action, detail)

    def recent_activity(self, limit: int = 200) -> list[ActivityLog]:
        return self._activity.recent(limit)

    # --- settings ---

    def welcome_message(self) -> str:
        return self._app_settings.get(KEY_WELCOME) or self._settings.welcome_message

    def greeting_message(self) -> str:
        """The general greeting (when none is set for the current time of day)."""
        return self._app_settings.get(KEY_GREETING) or DEFAULT_GREETING

    def greeting_at(self, now: datetime, language: str = LANG_THAI) -> str:
        """What the robot says first when the camera sees a visitor arrive at ``now``, or a place is picked.

        The Admin's own greeting (a time-of-day one, or a general one other than the
        built-in) is used as written, in Thai; otherwise the place's own greeting in ``language``,
        so the robot introduces the place it is answering for.
        """
        persona = self.persona()
        if language == LANG_ENGLISH:
            who = f"I'm {persona.english_name}, the reception robot" if persona.has_name else "I'm the reception robot"
            return self.venue().greeting_for(who, english=True) or ENGLISH_GREETING_REPLY
        greeting = persona.greeting_at(now)
        if greeting != DEFAULT_GREETING:
            return greeting
        who = f"ผมชื่อ{persona.thai_name} เป็นหุ่นยนต์ประชาสัมพันธ์" if persona.has_name else "ผมเป็นหุ่นยนต์ประชาสัมพันธ์"
        return self.venue().greeting_for(who) or greeting

    # --- persona ---

    def persona(self) -> Persona:
        """The robot's name, personality and set phrases (safe to call from any thread)."""
        values: dict[str, str] = {}
        stored_settings = self._app_settings.all()  # one query: this runs for every question
        for name, key in _PERSONA_KEYS.items():
            stored = stored_settings.get(key)
            if stored is None or (stored == "" and name in _PERSONA_REQUIRED):
                continue  # not set: the default
            values[name] = stored
        persona = Persona(**values)
        if persona.answer_length not in ANSWER_LENGTHS:
            persona = replace(persona, answer_length=Persona().answer_length)
        return persona

    def save_persona(self, persona: Persona) -> None:
        cleaned = {name: getattr(persona, name).strip() for name in _PERSONA_KEYS}
        labels = {
            "robot_name": "ชื่อหุ่นยนต์", "fallback": "ข้อความเมื่อตอบไม่ได้ (ไทย)",
            "fallback_en": "ข้อความเมื่อตอบไม่ได้ (อังกฤษ)", "greeting": "คำทักทายทั่วไป",
        }
        for name in _PERSONA_REQUIRED:
            if not cleaned[name]:
                raise ContentError(f"กรุณากรอก{labels[name]}")
        if len(cleaned["robot_name"]) > MAX_NAME_CHARS:
            raise ContentError(f"ชื่อหุ่นยนต์ยาวได้ไม่เกิน {MAX_NAME_CHARS} ตัวอักษร")
        if len(cleaned["role"]) > MAX_ROLE_CHARS:
            raise ContentError(f"บทบาทและบุคลิกยาวได้ไม่เกิน {MAX_ROLE_CHARS} ตัวอักษร")
        for name, text in cleaned.items():
            if name not in ("robot_name", "role", "answer_length") and len(text) > MAX_PHRASE_CHARS:
                raise ContentError(f"ข้อความแต่ละช่องยาวได้ไม่เกิน {MAX_PHRASE_CHARS} ตัวอักษร")
        if cleaned["answer_length"] not in ANSWER_LENGTHS:
            raise ContentError("ความยาวคำตอบไม่ถูกต้อง")
        previous = self.persona()
        for name, key in _PERSONA_KEYS.items():
            self._app_settings.set(key, cleaned[name])
        changed = [name for name in _PERSONA_KEYS if getattr(previous, name) != cleaned[name]]
        self._changed("persona.update", " ".join(changed) or "no change")

    def home_display(self) -> str:
        """What the big area of the home page shows: ``HOME_MEDIA`` or ``HOME_CAMERA``."""
        value = self._app_settings.get(KEY_HOME_DISPLAY, HOME_MEDIA)
        return value if value in HOME_DISPLAY_CHOICES else HOME_MEDIA

    def set_home_display(self, value: str) -> None:
        if value not in HOME_DISPLAY_CHOICES:
            raise ContentError("ตัวเลือกการแสดงผลหน้าแรกไม่ถูกต้อง")
        if value != self.home_display():
            self._app_settings.set(KEY_HOME_DISPLAY, value)
            self._changed("settings.update", f"home display={value}")

    def image_seconds(self) -> int:
        return self._int_setting(KEY_IMAGE_SECONDS, DEFAULT_IMAGE_SECONDS)

    def announcement_seconds(self) -> int:
        return self._int_setting(KEY_ANNOUNCEMENT_SECONDS, DEFAULT_ANNOUNCEMENT_SECONDS)

    def save_display_settings(
        self,
        welcome_message: str,
        image_seconds: int,
        announcement_seconds: int,
        greeting_message: str | None = None,
    ) -> None:
        welcome_message = welcome_message.strip()
        if not welcome_message:
            raise ContentError("กรุณากรอกข้อความต้อนรับ")
        if greeting_message is not None:
            greeting_message = greeting_message.strip()
            if not greeting_message:
                raise ContentError("กรุณากรอกคำทักทาย")
            self._app_settings.set(KEY_GREETING, greeting_message)
        for value in (image_seconds, announcement_seconds):
            if not MIN_SECONDS <= value <= MAX_SECONDS:
                raise ContentError(f"เวลาแสดงผลต้องอยู่ระหว่าง {MIN_SECONDS}–{MAX_SECONDS} วินาที")
        self._app_settings.set(KEY_WELCOME, welcome_message)
        self._app_settings.set(KEY_IMAGE_SECONDS, str(image_seconds))
        self._app_settings.set(KEY_ANNOUNCEMENT_SECONDS, str(announcement_seconds))
        self._changed("settings.update", "display settings")

    def default_privacy_notice(self) -> str:
        """A notice that says what this installation actually records (from its settings)."""
        settings = self._settings
        parts = ["หุ่นยนต์ใช้กล้องวัดระยะเพื่อทักทาย ไม่จดจำใบหน้า ไม่บันทึกภาพและเสียง"]
        if settings.log_questions:
            parts.append(
                f"ข้อความคำถามเก็บไว้ {settings.usage_retention_days} วันเพื่อปรับปรุงคำตอบ โดยไม่ระบุตัวตน"
            )
        else:
            parts.append("ไม่เก็บข้อความคำถาม")
        return " · ".join(parts)

    def privacy_notice(self) -> str:
        """What the visitor screen shows about privacy; "" when the Admin switched it off."""
        if self._app_settings.get(KEY_PRIVACY_ENABLED, "true") != "true":
            return ""
        return self._app_settings.get(KEY_PRIVACY_TEXT) or self.default_privacy_notice()

    def privacy_notice_text(self) -> str:
        """The notice text even when it is switched off (for editing)."""
        return self._app_settings.get(KEY_PRIVACY_TEXT) or self.default_privacy_notice()

    def privacy_notice_enabled(self) -> bool:
        return self._app_settings.get(KEY_PRIVACY_ENABLED, "true") == "true"

    def save_privacy_notice(self, enabled: bool, text: str) -> None:
        text = " ".join(text.split())
        if enabled and not text:
            raise ContentError("กรุณากรอกข้อความแจ้งความเป็นส่วนตัว หรือปิดการแสดง")
        if len(text) > MAX_PRIVACY_CHARS:
            raise ContentError(f"ข้อความแจ้งความเป็นส่วนตัวยาวได้ไม่เกิน {MAX_PRIVACY_CHARS} ตัวอักษร")
        # The generated text is not stored, so it keeps following the settings.
        self._app_settings.set(KEY_PRIVACY_TEXT, "" if text == self.default_privacy_notice() else text)
        self._app_settings.set(KEY_PRIVACY_ENABLED, "true" if enabled else "false")
        self._changed("settings.update", f"privacy notice enabled={enabled}")

    def mic_device_name(self) -> str | None:
        """The microphone chosen in Admin; None means the system default."""
        return self._app_settings.get(KEY_MIC_DEVICE) or None

    def set_mic_device_name(self, name: str | None) -> None:
        self._app_settings.set(KEY_MIC_DEVICE, name or "")
        self.log_activity(ACTOR_ADMIN, "settings.update", f"microphone={name or 'system default'}")

    def mic_channel(self) -> int:
        """Which channel of the microphone to listen to (matters for microphone arrays)."""
        try:
            return max(0, int(self._app_settings.get(KEY_MIC_CHANNEL, "0")))
        except (TypeError, ValueError):
            return 0

    def audio_output_name(self) -> str | None:
        """The speaker chosen in Admin; None means the system default."""
        return self._app_settings.get(KEY_AUDIO_OUTPUT) or None

    def save_audio_settings(
        self, mic_name: str | None, mic_channel: int, output_name: str | None
    ) -> None:
        if not 0 <= mic_channel <= MAX_MIC_CHANNEL:
            raise ContentError(f"ช่องสัญญาณไมโครโฟนต้องอยู่ระหว่าง 0–{MAX_MIC_CHANNEL}")
        self._app_settings.set(KEY_MIC_DEVICE, mic_name or "")
        self._app_settings.set(KEY_MIC_CHANNEL, str(mic_channel))
        self._app_settings.set(KEY_AUDIO_OUTPUT, output_name or "")
        self.log_activity(
            ACTOR_ADMIN, "settings.update",
            f"microphone={mic_name or 'system default'} channel={mic_channel} "
            f"speaker={output_name or 'system default'}",
        )

    def export_config(self, path: Path) -> int:
        """Write the Admin's settings (not content, not the password) to a JSON file.

        Returns how many values were written. Raises ``OSError``.
        """
        values = {
            key: value for key, value in self._app_settings.all().items() if _is_config_key(key)
        }
        document = {
            "format": CONFIG_FORMAT,
            "version": CONFIG_VERSION,
            "exported_at": datetime.now().isoformat(timespec="seconds"),
            "institution": self._settings.institution_name,
            "settings": values,
        }
        Path(path).write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8")
        self.log_activity(ACTOR_ADMIN, "config.export", f"values={len(values)} {Path(path).name}")
        return len(values)

    def import_config(self, path: Path) -> int:
        """Apply settings saved by ``export_config``. Returns how many. Raises ``ContentError``.

        Only known setting names are taken; anything else in the file is ignored.
        """
        try:
            document = json.loads(Path(path).read_text(encoding="utf-8"))
        except OSError:
            raise ContentError("เปิดไฟล์ไม่ได้") from None
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ContentError("ไฟล์นี้ไม่ใช่ไฟล์การตั้งค่าที่ส่งออกจากโปรแกรมนี้") from None
        if not isinstance(document, dict) or document.get("format") != CONFIG_FORMAT:
            raise ContentError("ไฟล์นี้ไม่ใช่ไฟล์การตั้งค่าที่ส่งออกจากโปรแกรมนี้")
        if not isinstance(document.get("version"), int) or document["version"] > CONFIG_VERSION:
            raise ContentError("ไฟล์การตั้งค่ามาจากโปรแกรมรุ่นที่ใหม่กว่า")
        values = document.get("settings")
        if not isinstance(values, dict):
            raise ContentError("ไฟล์การตั้งค่าไม่มีส่วน settings")
        applied = {
            key: value for key, value in values.items()
            if _is_config_key(key) and isinstance(value, str) and len(value) <= MAX_CONFIG_VALUE_CHARS
        }
        for key, value in applied.items():
            self._app_settings.set(key, value)
        self._changed("config.import", f"values={len(applied)} {Path(path).name}")
        return len(applied)

    def backup_database(self) -> Path:
        target = self._database.backup(self._settings.data_dir / "backups")
        self.log_activity(ACTOR_ADMIN, "database.backup", target.name)
        return target

    # --- announcements ---

    def announcements(self, enabled_only: bool = False, venue: str | None = None) -> list[Announcement]:
        """Announcements; with ``venue``, only that kind of place's and the shared ones."""
        return self._announcements.list(enabled_only, venue)

    def active_announcements(self) -> list[Announcement]:
        """What the screen shows and the AI may answer from now: the kind of place in use."""
        return self._announcements.list(True, self.venue_id())

    def save_announcement(
        self, item_id: int | None, title: str, body: str, enabled: bool, venue: str | None = None
    ) -> None:
        """``venue`` as for ``save_knowledge``: None = the kind in use (new) or unchanged (edit)."""
        title, body = title.strip(), body.strip()
        if not title:
            raise ContentError("กรุณากรอกหัวข้อประกาศ")
        if venue is not None and venue != venues.SHARED and not venues.is_known(venue):
            raise ContentError("ไม่รู้จักประเภทสถานที่นี้")
        if item_id is None:
            venue = self.venue_id() if venue is None else venue
            item_id = self._announcements.add(title, body, enabled, venue)
            self._changed("announcement.add", f"id={item_id} venue={venue or 'shared'} {title}")
        else:
            self._announcements.update(item_id, title, body, enabled, venue)
            self._changed("announcement.update", f"id={item_id} {title}")

    def delete_announcement(self, item_id: int) -> None:
        self._announcements.delete(item_id)
        self._changed("announcement.delete", f"id={item_id}")

    def move_announcement(self, item_id: int, direction: int) -> None:
        if self._announcements.move(item_id, direction):
            self.content_changed.emit()

    # --- kind of place ---

    def venue_id(self) -> str:
        """The kind of place in use; the default one if none (or an unknown one) was chosen."""
        return venues.get(self._app_settings.get(KEY_VENUE) or venues.DEFAULT_VENUE).id

    def venue(self) -> venues.Venue:
        return venues.get(self.venue_id())

    def place_name(self, venue_id: str | None = None) -> str:
        """The place's own name for this kind of place; "" if the Admin has not given one.

        A college set up before kinds of place existed keeps its name from app_config.json.
        """
        venue_id = venue_id or self.venue_id()
        stored = self._app_settings.get(KEY_VENUE_NAME + venue_id)
        if stored is not None:
            return stored
        return ""  # (a college's name from app_config.json was used here while colleges were offered)

    def start_in_default_venue(self) -> None:
        """Every start of the program begins in the default place (the invention contest), whatever
        place was in use when it was closed. The place's own name is left as it is."""
        if self.venue_id() != venues.DEFAULT_VENUE or self._app_settings.get(KEY_VENUE) != venues.DEFAULT_VENUE:
            self._app_settings.set(KEY_VENUE, venues.DEFAULT_VENUE)
            self._changed("venue.change", f"{venues.DEFAULT_VENUE} (program start)")

    def set_venue(self, venue_id: str, place_name: str = "") -> None:
        """Switch the robot to another kind of place (its knowledge, shortcuts and wording)."""
        if not venues.is_known(venue_id):
            raise ContentError("ไม่รู้จักประเภทสถานที่นี้")
        place_name = " ".join(place_name.split())
        if len(place_name) > MAX_PLACE_NAME_CHARS:
            raise ContentError(f"ชื่อสถานที่ยาวได้ไม่เกิน {MAX_PLACE_NAME_CHARS} ตัวอักษร")
        self._app_settings.set(KEY_VENUE, venue_id)
        self._app_settings.set(KEY_VENUE_NAME + venue_id, place_name)
        self._changed("venue.change", f"{venue_id} name={place_name or '-'}")

    def venue_auto(self) -> bool:
        """A question about another kind of place is answered from that place's data."""
        return self._app_settings.get(KEY_VENUE_AUTO) != "0"

    def set_venue_auto(self, enabled: bool) -> None:
        self._app_settings.set(KEY_VENUE_AUTO, "1" if enabled else "0")
        self._changed("venue.auto", "on" if enabled else "off")

    def knowledge_for(self, text: str) -> RoutedKnowledge:
        """What to answer ``text`` from: the place in use, or the place it is clearly about.

        Either way only one place's entries and announcements (plus the shared
        ones) are used, never a mixture. Raises on a database failure.
        """
        active = self.venue_id()
        home = Place(venues.get(active), self.place_name(active))
        everything = self._knowledge.list(True)
        route = None
        chosen = active
        if self.venue_auto():
            own: dict[str, list[KnowledgeItem]] = {}
            for item in everything:
                if item.venue != venues.SHARED:
                    own.setdefault(item.venue, []).append(item)
            route = venue_router.route(text, active, own)
            chosen = route.venue_id
        place = home if chosen == active else Place(venues.get(chosen), self.place_name(chosen))
        return RoutedKnowledge(
            items=[item for item in everything if item.venue in (chosen, venues.SHARED)],
            announcements=[
                f"{item.title} {item.body}".strip()
                for item in self._announcements.list(True, chosen)
            ],
            place=place,
            home=home,
            route=route,
        )

    # --- knowledge base ---

    def knowledge(self, enabled_only: bool = False, venue: str | None = None) -> list[KnowledgeItem]:
        """Entries; with ``venue``, only that kind of place's and the shared ones."""
        return self._knowledge.list(enabled_only, venue)

    def active_knowledge(self) -> list[KnowledgeItem]:
        """What the robot answers from now: in use, for the kind of place in use (or shared)."""
        return self._knowledge.list(True, self.venue_id())

    def shortcut_questions(self) -> list[str]:
        return [item.question for item in self.active_knowledge() if item.show_as_shortcut]

    def save_knowledge(
        self,
        item_id: int | None,
        category: str,
        question: str,
        answer: str,
        keywords: str,
        enabled: bool,
        show_as_shortcut: bool,
        expires_on: str = "",
        venue: str | None = None,
        question_en: str | None = None,
        answer_en: str | None = None,
    ) -> None:
        """``expires_on``: last day the entry is used, as typed (31/12/2569, 2026-12-31) or "".

        ``venue``: the kind of place it belongs to ("" = every kind). None: a new
        entry belongs to the kind in use, an edited one keeps its own.
        ``question_en`` / ``answer_en``: the entry in English ("" = Thai only; None = unchanged).
        """
        question_en = None if question_en is None else question_en.strip()
        answer_en = None if answer_en is None else answer_en.strip()
        if bool(question_en) != bool(answer_en) and question_en is not None and answer_en is not None:
            raise ContentError("ภาษาอังกฤษต้องกรอกทั้งคำถามและคำตอบ หรือเว้นว่างทั้งคู่")
        category, question, answer = category.strip(), question.strip(), answer.strip()
        if not question:
            raise ContentError("กรุณากรอกคำถามหรือหัวข้อ")
        if not answer:
            raise ContentError("กรุณากรอกคำตอบ")
        if venue is not None and venue != venues.SHARED and not venues.is_known(venue):
            raise ContentError("ไม่รู้จักประเภทสถานที่นี้")
        try:
            expires_on = parse_date(expires_on)
        except ValueError as exc:
            raise ContentError(str(exc)) from None
        keyword_list = [part.strip() for part in keywords.split(",") if part.strip()]
        if item_id is None:
            venue = self.venue_id() if venue is None else venue
            item_id = self._knowledge.add(
                category, question, answer, keyword_list, enabled, show_as_shortcut, expires_on,
                venue, question_en or "", answer_en or "",
            )
            self._changed("knowledge.add", f"id={item_id} venue={venue or 'shared'} {question}")
        else:
            self._knowledge.update(
                item_id, category, question, answer, keyword_list, enabled, show_as_shortcut,
                expires_on, venue, question_en, answer_en,
            )
            self._changed("knowledge.update", f"id={item_id} {question}")

    def delete_knowledge(self, item_id: int) -> None:
        self._knowledge.delete(item_id)
        self._changed("knowledge.delete", f"id={item_id}")

    def export_knowledge(self, path: Path) -> int:
        """Write every entry to a CSV file. Returns how many. Raises ``OSError``."""
        count = knowledge_transfer.export_csv(path, self._knowledge.list())
        self.log_activity(ACTOR_ADMIN, "knowledge.export", f"rows={count} {Path(path).name}")
        return count

    def import_knowledge(self, path: Path, keep_existing: bool = False) -> ImportReport:
        """Add the file's entries; an entry whose question already exists is updated instead.

        "Already exists" means the same question for the same kind of place: a
        hospital and a hotel may both answer "ที่จอดรถอยู่ที่ไหน". Rows that do
        not say which kind of place go to the kind in use.

        With ``keep_existing`` an existing entry is left as it is instead of updated.
        Raises ``ContentError`` if the file cannot be used at all.
        """
        try:
            rows, errors = knowledge_transfer.read_csv(path)
        except ValueError as exc:
            raise ContentError(str(exc)) from None
        except OSError:
            raise ContentError("เปิดไฟล์ไม่ได้ (ไฟล์อาจเปิดค้างอยู่ในโปรแกรมอื่น)") from None
        active = self.venue_id()

        def key(venue: str, question: str) -> tuple[str, str]:
            return venue, knowledge_transfer.question_key(question)

        existing = {key(item.venue, item.question): item for item in self._knowledge.list()}
        added = updated = 0
        for row in rows:
            venue = active if row.venue is None else row.venue
            keywords = [part.strip() for part in row.keywords.split(",") if part.strip()]
            match = existing.get(key(venue, row.question))
            if match is None:
                new_id = self._knowledge.add(
                    row.category, row.question, row.answer, keywords, row.enabled, row.shortcut,
                    row.expires_on, venue, row.question_en, row.answer_en,
                )
                existing[key(venue, row.question)] = KnowledgeItem(
                    new_id, row.category, row.question, row.answer, tuple(keywords), row.enabled,
                    row.shortcut, row.expires_on, venue, row.question_en, row.answer_en,
                )
                added += 1
            elif keep_existing:
                errors.append(f"มีอยู่แล้ว ไม่แก้ไข: {row.question} ({venues.label(venue)})")
            else:
                self._knowledge.update(
                    match.id, row.category, row.question, row.answer, keywords, row.enabled,
                    row.shortcut, row.expires_on, None, row.question_en, row.answer_en,
                )
                updated += 1
        report = ImportReport(added, updated, errors)
        self._changed(
            "knowledge.import",
            f"{Path(path).name} added={added} updated={updated} skipped={len(errors)}",
        )
        return report

    # --- cards and gestures ---

    def triggers(self) -> list[VisualTrigger]:
        return self._triggers.list()

    def find_trigger(self, kind: str, code: str) -> VisualTrigger | None:
        """The enabled reaction to this card or gesture, if the Admin has set one."""
        trigger = self._triggers.find(kind, code)
        return trigger if trigger is not None and trigger.enabled else None

    def save_trigger(
        self,
        trigger_id: int | None,
        kind: str,
        code: str,
        title: str,
        action: str,
        response_text: str,
        media_id: int | None,
        enabled: bool,
    ) -> None:
        code, title, response_text = code.strip(), title.strip(), response_text.strip()
        if kind == TRIGGER_CARD:
            if not code.isdigit() or not 0 <= int(code) < CARD_COUNT:
                raise ContentError(f"หมายเลขการ์ดต้องเป็นตัวเลข 0–{CARD_COUNT - 1}")
            code = str(int(code))
            action = ACTION_SAY  # a card always presents something
        elif kind == TRIGGER_GESTURE:
            if code not in GESTURE_LABELS:
                raise ContentError("ไม่รู้จักท่าทางนี้")
            if action not in TRIGGER_ACTIONS:
                raise ContentError("ไม่รู้จักการตอบสนองนี้")
        else:
            raise ContentError("ชนิดของสิ่งที่แสดงต่อกล้องไม่ถูกต้อง")
        if not title:
            raise ContentError("กรุณากรอกชื่อ")
        if action != ACTION_SAY:
            media_id = None
        elif not response_text and media_id is None:
            raise ContentError("กรุณากรอกข้อความที่จะพูด หรือเลือกสื่อที่จะแสดง")
        try:
            saved_id = self._triggers.save(
                trigger_id, kind, code, title, action, response_text, media_id, enabled
            )
        except sqlite3.IntegrityError as exc:
            raise ContentError("การ์ดหมายเลขนี้หรือท่าทางนี้ถูกตั้งค่าไว้แล้ว") from exc
        self._changed(
            "trigger.add" if trigger_id is None else "trigger.update",
            f"id={saved_id} {kind} {code} {title}",
        )

    def delete_trigger(self, trigger_id: int) -> None:
        self._triggers.delete(trigger_id)
        self._changed("trigger.delete", f"id={trigger_id}")

    # --- scheduled announcements ---

    def schedules(self) -> list[Schedule]:
        return self._schedules.list()

    def save_schedule(
        self,
        schedule_id: int | None,
        title: str,
        time_of_day: str,
        weekdays: str,
        message: str,
        media_id: int | None,
        enabled: bool,
    ) -> None:
        title, message = title.strip(), message.strip()
        if not title:
            raise ContentError("กรุณากรอกชื่อกำหนดการ")
        normalized_time = normalize_time(time_of_day)
        if normalized_time is None:
            raise ContentError("เวลาไม่ถูกต้อง กรุณากรอกแบบ 08:30")
        weekdays = normalize_weekdays(weekdays)
        if not weekdays:
            raise ContentError("กรุณาเลือกอย่างน้อยหนึ่งวัน")
        if not message and media_id is None:
            raise ContentError("กรุณากรอกข้อความที่จะประกาศ หรือเลือกสื่อที่จะแสดง")
        saved_id = self._schedules.save(
            schedule_id, title, normalized_time, weekdays, message, media_id, enabled
        )
        self._changed(
            "schedule.add" if schedule_id is None else "schedule.update",
            f"id={saved_id} {normalized_time} {title}",
        )

    def delete_schedule(self, schedule_id: int) -> None:
        self._schedules.delete(schedule_id)
        self._changed("schedule.delete", f"id={schedule_id}")

    # --- media ---

    def media(self) -> list[MediaItem]:
        return self._media.list()

    def playlist(self) -> list[tuple[MediaItem, Path]]:
        return self._media.playlist()

    def media_path(self, item: MediaItem) -> Path:
        """Raises ``MediaError`` if the stored name is not a safe media file name."""
        return self._media.resolve_path(item)

    def import_media(self, source: Path, title: str, description: str, category: str) -> None:
        """Copy a file into the media directory in the background.

        The result arrives through ``media_import_finished``.
        """
        worker = FunctionWorker(None, self._import, source, title, description, category)
        worker.signals.finished.connect(self._on_import_finished)
        worker.signals.failed.connect(self._on_import_failed)
        self._pool.start(worker)

    def update_media(
        self, item_id: int, title: str, description: str, category: str, enabled: bool
    ) -> None:
        try:
            self._media.update(item_id, title, description, category, enabled)
        except MediaError as exc:
            raise ContentError(str(exc)) from exc
        self._changed("media.update", f"id={item_id} {title.strip()}")

    def delete_media(self, item_id: int) -> None:
        self._media.delete(item_id)
        self._changed("media.delete", f"id={item_id}")

    def move_media(self, item_id: int, direction: int) -> None:
        if self._media.move(item_id, direction):
            self.content_changed.emit()

    # --- internals ---

    def _import(self, source: Path, title: str, description: str, category: str) -> object:
        # Runs in a worker thread. A refusal is an expected outcome, so it is
        # returned rather than raised; only real failures reach _on_import_failed.
        try:
            return self._media.import_file(source, title, description, category)
        except MediaError as exc:
            return exc

    @Slot(object, object)
    def _on_import_finished(self, _token: object, result: object) -> None:
        if isinstance(result, MediaError):
            self.media_import_finished.emit(None, str(result))
            return
        self._changed("media.add", f"id={result.id} {result.title} ({result.original_name})")
        self.media_import_finished.emit(result, "")

    @Slot(object, str)
    def _on_import_failed(self, _token: object, _error: str) -> None:
        self.media_import_finished.emit(None, "เพิ่มสื่อไม่สำเร็จ ดูรายละเอียดในบันทึกการทำงาน")

    def _changed(self, action: str, detail: str) -> None:
        self.log_activity(ACTOR_ADMIN, action, detail)
        self.content_changed.emit()

    def _int_setting(self, key: str, default: int) -> int:
        try:
            value = int(self._app_settings.get(key, str(default)))
        except (TypeError, ValueError):
            return default
        return value if MIN_SECONDS <= value <= MAX_SECONDS else default

    def _seed_default_triggers(self) -> None:
        """Give the built-in gestures their default meaning, once (also on upgraded databases)."""
        self._seed_more_gestures()
        self._seed_intro_gesture()
        self._seed_six_seven_gesture()
        self._seed_wave_gestures()
        if self._app_settings.get(KEY_TRIGGERS_SEEDED):
            return
        defaults = (
            ("Thumb_Up", "ยกนิ้วให้คำตอบ", ACTION_FEEDBACK_UP, "ขอบคุณครับ ดีใจที่ช่วยได้ครับ"),
            ("Thumb_Down", "คำตอบยังไม่ตรง", ACTION_FEEDBACK_DOWN,
             "ขออภัยครับ ผมจะปรับปรุงให้ดีขึ้น ลองถามเจ้าหน้าที่ประชาสัมพันธ์เพิ่มเติมได้ครับ"),
            ("Open_Palm", "แบมือเพื่อให้หยุด", ACTION_STOP, ""),
        )
        for code, title, action, text in defaults:
            if self._triggers.find(TRIGGER_GESTURE, code) is None:
                self._triggers.save(None, TRIGGER_GESTURE, code, title, action, text, None, True)
        self._app_settings.set(KEY_TRIGGERS_SEEDED, "1")

    def _seed_wave_gestures(self) -> None:
        """A visitor waving goodbye (an open hand swung from side to side) is answered with a goodbye, once; the Admin may
        change the words."""
        if self._app_settings.get(KEY_WAVES_SEEDED):
            return
        for code, title in (("Wave_Visitor_Right", "โบกมือขวา (บ๊ายบาย)"), ("Wave_Visitor_Left", "โบกมือซ้าย (บ๊ายบาย)")):
            if self._triggers.find(TRIGGER_GESTURE, code) is None:
                self._triggers.save(None, TRIGGER_GESTURE, code, title, ACTION_SAY, "บ๊ายบายครับ ไว้พบกันใหม่นะครับ", None, True)
        self._app_settings.set(KEY_WAVES_SEEDED, "1")

    def _seed_six_seven_gesture(self) -> None:
        """The "6 7" gesture (two hands up and down in turn) is answered with "Six seven." and the same arm movement (10 Nov
        2026), once; the Admin may change the words."""
        if self._app_settings.get(KEY_SIX_SEVEN_SEEDED):
            return
        if self._triggers.find(TRIGGER_GESTURE, "Six_Seven") is None:
            self._triggers.save(None, TRIGGER_GESTURE, "Six_Seven", "ท่า 67", ACTION_SAY, "Six seven.", None, True)
        self._app_settings.set(KEY_SIX_SEVEN_SEEDED, "1")

    def _seed_intro_gesture(self) -> None:
        """The "finger pointing sideways, thumb up" sign introduces the robot to the committee (added 2026-11-08), once."""
        if self._app_settings.get(KEY_INTRO_GESTURE_SEEDED):
            return
        if self._triggers.find(TRIGGER_GESTURE, "Finger_Gun") is None:
            self._triggers.save(None, TRIGGER_GESTURE, "Finger_Gun", "แนะนำตัวต่อกรรมการ", ACTION_SAY,
                                INTRO_SPEECH, None, True)
        self._app_settings.set(KEY_INTRO_GESTURE_SEEDED, "1")

    def _seed_more_gestures(self) -> None:
        """Meanings for the other gestures (added 2026-11-06), once; a gesture the Admin has set stays as it is."""
        if self._app_settings.get(KEY_GESTURES_SEEDED):
            return
        defaults = (
            ("ILoveYou", "บอกรัก", "ขอบคุณครับ ผมก็รักทุกคนเลยครับ"),
            ("Victory", "ชูสองนิ้วถ่ายรูป", "ยิ้มกว้าง ๆ ครับ ถ่ายรูปกับผมได้เลยครับ"),
            ("Closed_Fist", "กำมือให้กำลังใจ", "สู้ ๆ ครับ ขอให้ทุกทีมโชคดีในการแข่งขันครับ"),
            ("Pointing_Up", "ชี้นิ้วขึ้นขอถาม", "มีคำถามใช่ไหมครับ ถามผมได้เลยครับ"),
        )
        for code, title, text in defaults:
            if self._triggers.find(TRIGGER_GESTURE, code) is None:
                self._triggers.save(None, TRIGGER_GESTURE, code, title, ACTION_SAY, text, None, True)
        self._app_settings.set(KEY_GESTURES_SEEDED, "1")

    def _seed_if_first_run(self) -> None:
        """Copy the starter content from ``app_config.json`` into an empty database."""
        if self._app_settings.get(KEY_SEEDED):
            return
        for text in self._settings.announcements:
            self._announcements.add(text, "", venue=self.venue_id())
        for item in self._settings.faq:
            self._knowledge.add("FAQ", item.question, item.answer, item.keywords,
                                enabled=True, show_as_shortcut=True, venue=self.venue_id())
        self._app_settings.set(KEY_WELCOME, self._settings.welcome_message)
        self._app_settings.set(KEY_SEEDED, "1")
        self.log_activity(ACTOR_SYSTEM, "content.seed", "starter content from app_config.json")
        logger.info("Seeded database with starter content")
