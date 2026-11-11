"""Application settings.

Secrets and machine-specific values come from environment variables / ``.env``.
Non-secret content (texts, FAQ, robot allowlist) comes from ``app_config.json``.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = BASE_DIR / "config" / "app_config.json"
DEFAULT_ENV_PATH = BASE_DIR / ".env"

# Robot action ids: the only shape of parameter a robot command may carry.
ID_PATTERN = re.compile(r"[a-z][a-z0-9_]{0,63}")
SUPPORTED_ADAPTERS = ("mock",)
LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")


class ConfigError(Exception):
    """Raised when configuration is missing or invalid."""


PART_NONE = "none"
PART_BASE = "base"  # the robot has no arms: turning on the spot is its only movement
ROBOT_PARTS = (PART_NONE, PART_BASE)


@dataclass(frozen=True)
class NamedTarget:
    id: str
    label: str
    # Other ways a visitor may say it ("ลอบบี้", "lobby"); the label always counts too.
    phrases: tuple[str, ...] = ()
    # Actions only: which part of the robot moves, which decides how much room it needs.
    part: str = PART_NONE


@dataclass(frozen=True)
class FaqItem:
    question: str
    answer: str
    keywords: tuple[str, ...] = ()


@dataclass(frozen=True)
class Settings:
    institution_name: str
    # welcome_message, announcements and faq only seed the database on first
    # run; after that the Admin screen is the place to edit them.
    welcome_message: str
    announcements: tuple[str, ...]
    faq: tuple[FaqItem, ...]
    actions: tuple[NamedTarget, ...]
    data_dir: Path
    log_dir: Path
    media_dir: Path
    log_level: str
    robot_adapter: str
    command_timeout_s: float
    idle_timeout_s: int
    # The Admin screen's own idle logout; 0 = it stays until the Admin logs out.
    admin_idle_timeout_s: int
    fullscreen: bool
    max_video_bytes: int
    max_image_bytes: int
    model_dir: Path
    stt_enabled: bool
    stt_model: str
    stt_device: str
    stt_compute_type: str
    stt_language: str
    stt_thai_model: str  # a Whisper model trained for Thai; "" = Thai uses stt_model
    stt_thai_model_revision: str | None
    stt_detector_model: str
    mic_level_threshold: float
    mic_silence_s: float
    mic_no_speech_timeout_s: float
    mic_max_seconds: float
    mic_array_required: bool  # the screen says the microphone is ready only while the XVF3800 is plugged in
    voice_follow_up: bool
    ai_enabled: bool
    gemini_model: str
    gemini_timeout_s: float
    # A question a knowledge-base entry clearly answers gets that entry at once, without the AI.
    ai_knowledge_first: bool
    tts_enabled: bool
    tts_speed: float
    vision_enabled: bool
    vision_model: str
    vision_fps: int
    vision_greet_distance_m: float
    vision_greet_dwell_s: float
    vision_engage_distance_m: float
    vision_release_distance_m: float
    vision_lateral_limit_m: float
    vision_dwell_s: float
    vision_leave_s: float
    vision_greet_cooldown_s: float
    vision_auto_listen: bool
    vision_preview: bool
    vision_preview_fps: int
    vision_signs: bool
    vision_gestures: bool
    log_questions: bool
    usage_retention_days: int
    voice_commands: bool
    greet_action: str  # action id performed when a visitor arrives; "" = none
    base_clearance_m: float
    # repr=False keeps the key out of logs and tracebacks that print the settings object.
    gemini_api_key: str = field(default="", repr=False)
    # MQTT link for hardware communication tests (off unless MQTT_ENABLED=true).
    mqtt_enabled: bool = False
    mqtt_host: str = "127.0.0.1"
    mqtt_port: int = 1883
    mqtt_client_id: str = "prrobot-app"
    mqtt_username: str = ""
    mqtt_password: str = field(default="", repr=False)
    mqtt_tls: bool = False
    mqtt_ca_file: str = ""
    mqtt_topic_prefix: str = "prrobot/test"
    mqtt_response_timeout_s: float = 3.0
    # MQTT link to the real hardware (plain-text direction commands from the Admin).
    # Off while MQTT_HW_HOST is empty; direction commands stay off until a STOP message is set.
    hw_mqtt_host: str = ""
    hw_mqtt_port: int = 1883
    hw_mqtt_username: str = ""
    hw_mqtt_password: str = field(default="", repr=False)
    hw_direction_topic: str = "robot/direction"  # where STOP always goes
    # Where turns (left/right, degrees) and moves (forward/backward, cm) go to begin with.
    hw_turn_topic: str = "robot/direction"
    hw_move_topic: str = "robot/direction"
    # Topics the control page offers to send to (the Admin may type more).
    hw_topics: tuple[str, ...] = ("robot/direction", "robot/status", "robot/mode")
    hw_stop_payload: str = ""
    hw_reply_topic: str = ""
    hw_status_topic: str = "robot/r1/status"
    hw_payload_format: str = "{direction} {value}"
    hw_turn_max_deg: int = 180
    hw_move_max_cm: int = 100
    hw_require_camera: bool = False
    # Voice direction (DOA): which microphone measures it, and where it sits relative to the camera.
    audio_direction_source: str = "none"
    mic_offset_x_m: float = 0.0
    mic_offset_z_m: float = 0.0
    mic_yaw_deg: float = 0.0
    camera_hfov_deg: float = 69.0
    speaker_match_tolerance_deg: float = 15.0
    # Loud sounds that a voice detector says are not speech never start or hold open a turn.
    mic_voice_filter: bool = True
    mic_speaker_lock: bool = True  # listen to one voice: the visitor who started the turn
    # The robot greets a visitor who has stopped in front of the camera for this long (a person walking past is not
    # greeted). 0 = greet whoever arrives, as before.
    vision_greet_still_s: float = 1.5

    @property
    def demo_mode(self) -> bool:
        """True when answers come from the knowledge base only (no key, or AI switched off)."""
        return not (self.gemini_api_key and self.ai_enabled)


def load_settings(
    config_path: Path = DEFAULT_CONFIG_PATH,
    environ: Mapping[str, str] | None = None,
) -> Settings:
    """Load settings. Pass ``environ`` explicitly (tests) to skip reading ``.env``."""
    if environ is None:
        load_dotenv(DEFAULT_ENV_PATH)
        environ = os.environ

    raw = _read_json(config_path)
    robot = _section(raw, "robot")

    robot_adapter = environ.get("ROBOT_ADAPTER", "mock").strip().lower() or "mock"
    if robot_adapter not in SUPPORTED_ADAPTERS:
        raise ConfigError(
            f"ROBOT_ADAPTER='{robot_adapter}' ยังไม่รองรับในรุ่นนี้ "
            f"(ใช้ได้: {', '.join(SUPPORTED_ADAPTERS)})"
        )

    log_level = environ.get("LOG_LEVEL", "INFO").strip().upper() or "INFO"
    if log_level not in LOG_LEVELS:
        raise ConfigError(f"LOG_LEVEL='{log_level}' ไม่ถูกต้อง (ใช้ได้: {', '.join(LOG_LEVELS)})")

    # Three rings around the robot, from the inside out: talk, greet, gone.
    engage_distance = _positive_float(environ, "VISION_ENGAGE_DISTANCE_M", 1.5)
    greet_distance = _positive_float(environ, "VISION_GREET_DISTANCE_M", 3.0)
    release_distance = _positive_float(environ, "VISION_RELEASE_DISTANCE_M", 3.6)
    if not engage_distance <= greet_distance <= release_distance:
        raise ConfigError(
            "ระยะกล้องต้องเรียงจากใกล้ไปไกล: VISION_ENGAGE_DISTANCE_M ≤ "
            "VISION_GREET_DISTANCE_M ≤ VISION_RELEASE_DISTANCE_M"
        )

    actions = tuple(_targets(robot, "actions"))
    greet_action = robot.get("greet_action", "")
    if greet_action and greet_action not in {action.id for action in actions}:
        raise ConfigError(f"'robot.greet_action' ต้องเป็น id ที่อยู่ใน 'robot.actions' (ได้ '{greet_action}')")

    return Settings(
        institution_name=_text(raw, "institution_name"),
        welcome_message=_text(raw, "welcome_message"),
        announcements=tuple(_text_list(raw, "announcements")),
        faq=tuple(_faq_items(raw)),
        actions=actions,
        voice_commands=_bool(environ, "ROBOT_VOICE_COMMANDS", True),
        greet_action=greet_action,
        base_clearance_m=_positive_float(environ, "ROBOT_BASE_CLEARANCE_M", 0.6),
        data_dir=_path(environ, "DATA_DIR", "data"),
        log_dir=_path(environ, "LOG_DIR", "logs"),
        media_dir=_path(environ, "MEDIA_DIR", "media"),
        log_level=log_level,
        robot_adapter=robot_adapter,
        command_timeout_s=_positive_float(environ, "ROBOT_COMMAND_TIMEOUT_S", 30.0),
        idle_timeout_s=int(_positive_float(environ, "IDLE_TIMEOUT_S", 90.0)),
        admin_idle_timeout_s=_not_negative(environ, "ADMIN_IDLE_TIMEOUT_S", 0),
        fullscreen=_bool(environ, "UI_FULLSCREEN", False),
        max_video_bytes=int(_positive_float(environ, "MEDIA_MAX_VIDEO_MB", 500.0) * 1_048_576),
        max_image_bytes=int(_positive_float(environ, "MEDIA_MAX_IMAGE_MB", 20.0) * 1_048_576),
        model_dir=_path(environ, "MODEL_DIR", "models"),
        stt_enabled=_bool(environ, "STT_ENABLED", True),
        stt_model=_word(environ, "STT_MODEL", "small"),
        stt_device=_choice(environ, "STT_DEVICE", "cpu", ("cpu", "cuda", "auto")),
        stt_compute_type=_word(environ, "STT_COMPUTE_TYPE", "int8"),
        stt_language=_word(environ, "STT_LANGUAGE", "th"),
        **_thai_speech_model(environ),
        mic_level_threshold=_positive_float(environ, "MIC_LEVEL_THRESHOLD", 0.02),
        mic_silence_s=_positive_float(environ, "MIC_SILENCE_SECONDS", 0.5),
        mic_no_speech_timeout_s=_positive_float(environ, "MIC_NO_SPEECH_TIMEOUT_S", 6.0),
        mic_max_seconds=_positive_float(environ, "MIC_MAX_SECONDS", 20.0),
        mic_array_required=_bool(environ, "MIC_ARRAY_REQUIRED", True),
        voice_follow_up=_bool(environ, "VOICE_FOLLOW_UP", True),
        ai_enabled=_bool(environ, "AI_ENABLED", True),
        # 3.1-flash-lite: ~2 s per answer when measured on 2026-11-04, while 3.5-flash-lite took 13-27 s.
        gemini_model=_word(environ, "GEMINI_MODEL", "gemini-3.5-flash-lite"),
        gemini_timeout_s=_positive_float(environ, "GEMINI_TIMEOUT_S", 4.5),
        ai_knowledge_first=_bool(environ, "AI_KNOWLEDGE_FIRST", True),
        tts_enabled=_bool(environ, "TTS_ENABLED", True),
        tts_speed=_positive_float(environ, "TTS_SPEED", 0.9),
        vision_enabled=_bool(environ, "VISION_ENABLED", True),
        vision_model=_word(environ, "VISION_MODEL", "yolov6-nano"),
        vision_fps=int(_positive_float(environ, "VISION_FPS", 10.0)),
        vision_greet_distance_m=greet_distance,
        vision_greet_dwell_s=_positive_float(environ, "VISION_GREET_DWELL_SECONDS", 1.5),
        vision_engage_distance_m=engage_distance,
        vision_release_distance_m=release_distance,
        vision_lateral_limit_m=_positive_float(environ, "VISION_LATERAL_LIMIT_M", 1.0),
        vision_dwell_s=_positive_float(environ, "VISION_DWELL_SECONDS", 0.8),
        vision_leave_s=_positive_float(environ, "VISION_LEAVE_SECONDS", 4.0),
        vision_greet_cooldown_s=_positive_float(environ, "VISION_GREET_COOLDOWN_SECONDS", 12.0),
        vision_auto_listen=_bool(environ, "VISION_AUTO_LISTEN", True),
        vision_preview=_bool(environ, "VISION_PREVIEW", True),
        vision_preview_fps=int(_positive_float(environ, "VISION_PREVIEW_FPS", 20.0)),
        vision_signs=_bool(environ, "VISION_SIGNS", True),
        vision_gestures=_bool(environ, "VISION_GESTURES", True),
        log_questions=_bool(environ, "LOG_QUESTIONS", True),
        usage_retention_days=int(_positive_float(environ, "USAGE_RETENTION_DAYS", 90.0)),
        gemini_api_key=environ.get("GEMINI_API_KEY", "").strip(),
        mqtt_enabled=_bool(environ, "MQTT_ENABLED", False),
        mqtt_host=_word(environ, "MQTT_HOST", "127.0.0.1"),
        mqtt_port=_port(environ, "MQTT_PORT", 1883),
        mqtt_client_id=_word(environ, "MQTT_CLIENT_ID", "prrobot-app"),
        mqtt_username=environ.get("MQTT_USERNAME", "").strip(),
        mqtt_password=environ.get("MQTT_PASSWORD", ""),
        mqtt_tls=_bool(environ, "MQTT_TLS", False),
        mqtt_ca_file=environ.get("MQTT_CA_FILE", "").strip(),
        mqtt_topic_prefix=_word(environ, "MQTT_TOPIC_PREFIX", "prrobot/test").strip("/"),
        mqtt_response_timeout_s=_positive_float(environ, "MQTT_RESPONSE_TIMEOUT_S", 3.0),
        audio_direction_source=_choice(environ, "AUDIO_DIRECTION_SOURCE", "none", ("none", "xvf3800")),
        mic_offset_x_m=_float(environ, "MIC_OFFSET_X_M", 0.0),
        mic_offset_z_m=_float(environ, "MIC_OFFSET_Z_M", 0.0),
        mic_yaw_deg=_float(environ, "MIC_YAW_DEG", 0.0),
        camera_hfov_deg=_positive_float(environ, "CAMERA_HFOV_DEG", 69.0),
        speaker_match_tolerance_deg=_positive_float(environ, "SPEAKER_MATCH_TOLERANCE_DEG", 15.0),
        mic_voice_filter=_bool(environ, "MIC_VOICE_FILTER", True),
        mic_speaker_lock=_bool(environ, "MIC_SPEAKER_LOCK", True),
        vision_greet_still_s=max(0.0, _float(environ, "VISION_GREET_STILL_SECONDS", 1.5)),
        **_hardware_link(environ),
    )


# Thonburian Whisper medium (Mahidol University, Apache-2.0) in CTranslate2 form,
# pinned: about 1.5 GB in models/, much better Thai than the general models.
THAI_SPEECH_MODEL = "Thaweewat/whisper-th-medium-ct2"
THAI_SPEECH_MODEL_REVISION = "928ce1ae8acf6c14bfa0afc0ec5fe308a07f0f1a"


def _thai_speech_model(environ: Mapping[str, str]) -> dict[str, Any]:
    """STT_THAI_MODEL: the default Thai model, another one, or "none" for the general model only."""
    name = environ.get("STT_THAI_MODEL", "").strip()
    if name.lower() == "none":
        return {"stt_thai_model": "", "stt_thai_model_revision": None,
                "stt_detector_model": _word(environ, "STT_DETECTOR_MODEL", "tiny")}
    model = _word(environ, "STT_THAI_MODEL", THAI_SPEECH_MODEL)
    return {
        "stt_thai_model": model,
        # The pinned revision belongs to the default model only.
        "stt_thai_model_revision": THAI_SPEECH_MODEL_REVISION if model == THAI_SPEECH_MODEL else None,
        "stt_detector_model": _word(environ, "STT_DETECTOR_MODEL", "tiny"),
    }


_PAYLOAD_TEXT = re.compile(r"[^\x00-\x1f\x7f]{1,100}")


def _hardware_link(environ: Mapping[str, str]) -> dict[str, Any]:
    """MQTT_HW_*: the hardware link's broker, topics and the messages it may send."""
    host = environ.get("MQTT_HW_HOST", "").strip()
    if host and not re.fullmatch(r"[A-Za-z0-9.\-]{1,100}", host):
        raise ConfigError(f"MQTT_HW_HOST='{host}' มีอักขระที่ไม่อนุญาต")
    direction_topic = _word(environ, "MQTT_HW_DIRECTION_TOPIC", "robot/direction")
    reply_topic = environ.get("MQTT_HW_REPLY_TOPIC", "").strip()
    if reply_topic and not re.fullmatch(r"[A-Za-z0-9._/\-]{1,100}#?", reply_topic):
        raise ConfigError(f"MQTT_HW_REPLY_TOPIC='{reply_topic}' มีอักขระที่ไม่อนุญาต")
    if reply_topic and reply_topic == direction_topic:
        raise ConfigError("MQTT_HW_REPLY_TOPIC ต้องไม่ใช่ topic เดียวกับ MQTT_HW_DIRECTION_TOPIC "
                          "(ข้อความที่เราส่งเองจะถูกนับเป็นคำตอบของฮาร์ดแวร์)")
    status_topic = _word(environ, "MQTT_HW_STATUS_TOPIC", "robot/r1/status")
    if status_topic == direction_topic:
        raise ConfigError("MQTT_HW_STATUS_TOPIC ต้องไม่ใช่ topic ที่เราส่งคำสั่งออกไป "
                          "(ข้อความของเราเองจะถูกนับเป็นสถานะของหุ่นยนต์)")
    turn_topic = _word(environ, "MQTT_HW_TURN_TOPIC", direction_topic)
    move_topic = _word(environ, "MQTT_HW_MOVE_TOPIC", direction_topic)
    topics = list(dict.fromkeys((direction_topic, turn_topic, move_topic)))
    for topic in environ.get("MQTT_HW_TOPICS", "robot/direction,robot/status,robot/mode").split(","):
        topic = topic.strip()
        if topic and topic not in topics:
            topics.append(topic)
    for topic in topics:
        if not re.fullmatch(r"[A-Za-z0-9._/\-]{1,100}", topic):
            raise ConfigError(f"MQTT_HW_TOPICS มี topic ที่ไม่ถูกต้อง: '{topic}'")
        if topic in (status_topic, reply_topic):
            raise ConfigError(f"ส่งคำสั่งไป '{topic}' ไม่ได้ เพราะเป็น topic ที่รับข้อความจากหุ่นยนต์")
    stop_payload = environ.get("MQTT_HW_STOP_PAYLOAD", "").strip()
    if stop_payload and not _PAYLOAD_TEXT.fullmatch(stop_payload):
        raise ConfigError("MQTT_HW_STOP_PAYLOAD ต้องเป็นข้อความบรรทัดเดียว ยาวไม่เกิน 100 ตัวอักษร")
    payload_format = environ.get("MQTT_HW_PAYLOAD_FORMAT", "").strip() or "{direction} {value}"
    try:
        sample = payload_format.format(direction="left", value=50)
    except (KeyError, IndexError, ValueError) as exc:
        raise ConfigError(
            f"MQTT_HW_PAYLOAD_FORMAT='{payload_format}' ใช้ได้เฉพาะ {{direction}} และ {{value}}"
        ) from exc
    if "{direction}" not in payload_format or "{value}" not in payload_format or not _PAYLOAD_TEXT.fullmatch(sample):
        raise ConfigError("MQTT_HW_PAYLOAD_FORMAT ต้องมีทั้ง {direction} และ {value} เป็นข้อความบรรทัดเดียว")
    turn_max = _whole(environ, "MQTT_HW_TURN_MAX_DEG", 180)
    if not 1 <= turn_max <= 360:
        raise ConfigError("MQTT_HW_TURN_MAX_DEG ต้องเป็น 1–360 องศา")
    move_max = _whole(environ, "MQTT_HW_MOVE_MAX_CM", 100)
    if not 1 <= move_max <= 1000:
        raise ConfigError("MQTT_HW_MOVE_MAX_CM ต้องเป็น 1–1000 ซม.")
    return {
        "hw_mqtt_host": host,
        "hw_mqtt_port": _port(environ, "MQTT_HW_PORT", 1883),
        "hw_mqtt_username": environ.get("MQTT_HW_USERNAME", "").strip(),
        "hw_mqtt_password": environ.get("MQTT_HW_PASSWORD", ""),
        "hw_direction_topic": direction_topic,
        "hw_turn_topic": turn_topic,
        "hw_move_topic": move_topic,
        "hw_topics": tuple(topics),
        "hw_stop_payload": stop_payload,
        "hw_reply_topic": reply_topic,
        "hw_status_topic": status_topic,
        "hw_payload_format": payload_format,
        "hw_turn_max_deg": turn_max,
        "hw_move_max_cm": move_max,
        "hw_require_camera": _bool(environ, "MQTT_HW_REQUIRE_CAMERA", False),
    }


def _not_negative(environ: Mapping[str, str], key: str, default: int) -> int:
    value = _whole(environ, key, default)
    if value < 0:
        raise ConfigError(f"{key} ต้องเป็น 0 หรือมากกว่า")
    return value


def _whole(environ: Mapping[str, str], key: str, default: int) -> int:
    text = environ.get(key, "").strip()
    if not text:
        return default
    try:
        return int(text)
    except ValueError as exc:
        raise ConfigError(f"{key}='{text}' ต้องเป็นจำนวนเต็ม") from exc


def _read_json(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConfigError(f"ไม่พบไฟล์ตั้งค่า: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ConfigError(f"ไฟล์ตั้งค่า {path} ไม่ใช่ JSON ที่ถูกต้อง: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError(f"ไฟล์ตั้งค่า {path} ต้องเป็น JSON object")
    return raw


def _section(raw: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    value = raw.get(key)
    if not isinstance(value, dict):
        raise ConfigError(f"ไฟล์ตั้งค่าขาดส่วน '{key}'")
    return value


def _text(raw: Mapping[str, Any], key: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"ไฟล์ตั้งค่าขาดข้อความ '{key}'")
    return value


def _text_list(raw: Mapping[str, Any], key: str) -> list[str]:
    value = raw.get(key, [])
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ConfigError(f"'{key}' ในไฟล์ตั้งค่าต้องเป็นรายการข้อความ")
    return value


def _faq_items(raw: Mapping[str, Any]) -> list[FaqItem]:
    items = raw.get("faq", [])
    if not isinstance(items, list):
        raise ConfigError("'faq' ในไฟล์ตั้งค่าต้องเป็นรายการ")
    result = []
    for item in items:
        if not isinstance(item, dict):
            raise ConfigError("แต่ละรายการใน 'faq' ต้องเป็น object")
        result.append(
            FaqItem(
                question=_text(item, "question"),
                answer=_text(item, "answer"),
                keywords=tuple(_text_list(item, "keywords")),
            )
        )
    return result


def _targets(raw: Mapping[str, Any], key: str) -> list[NamedTarget]:
    items = raw.get(key, [])
    if not isinstance(items, list):
        raise ConfigError(f"'robot.{key}' ในไฟล์ตั้งค่าต้องเป็นรายการ")
    result: list[NamedTarget] = []
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            raise ConfigError(f"แต่ละรายการใน 'robot.{key}' ต้องเป็น object")
        target_id = item.get("id")
        if not isinstance(target_id, str) or not ID_PATTERN.fullmatch(target_id):
            raise ConfigError(
                f"id '{target_id}' ใน 'robot.{key}' ไม่ถูกต้อง "
                "(ใช้ a-z, 0-9, _ ขึ้นต้นด้วยตัวอักษร ยาวไม่เกิน 64)"
            )
        if target_id in seen:
            raise ConfigError(f"id '{target_id}' ใน 'robot.{key}' ซ้ำกัน")
        seen.add(target_id)
        part = item.get("part", PART_NONE)
        if part not in ROBOT_PARTS:
            raise ConfigError(
                f"'part' ของ '{target_id}' ใน 'robot.{key}' ต้องเป็นหนึ่งใน {', '.join(ROBOT_PARTS)}"
            )
        result.append(
            NamedTarget(
                id=target_id, label=_text(item, "label"),
                phrases=tuple(_text_list(item, "phrases")), part=part,
            )
        )
    return result


def _path(environ: Mapping[str, str], key: str, default: str) -> Path:
    path = Path(environ.get(key, "").strip() or default)
    return path if path.is_absolute() else BASE_DIR / path


def _word(environ: Mapping[str, str], key: str, default: str) -> str:
    """A model name / option token: letters, digits, dot, dash, underscore, slash."""
    text = environ.get(key, "").strip() or default
    if not re.fullmatch(r"[A-Za-z0-9._/\-]{1,100}", text):
        raise ConfigError(f"{key}='{text}' มีอักขระที่ไม่อนุญาต")
    return text


def _choice(environ: Mapping[str, str], key: str, default: str, allowed: tuple[str, ...]) -> str:
    text = environ.get(key, "").strip().lower() or default
    if text not in allowed:
        raise ConfigError(f"{key}='{text}' ไม่ถูกต้อง (ใช้ได้: {', '.join(allowed)})")
    return text


def _positive_float(environ: Mapping[str, str], key: str, default: float) -> float:
    text = environ.get(key, "").strip()
    if not text:
        return default
    try:
        value = float(text)
    except ValueError as exc:
        raise ConfigError(f"{key}='{text}' ต้องเป็นตัวเลข") from exc
    if value <= 0:
        raise ConfigError(f"{key} ต้องมากกว่า 0")
    return value


def _float(environ: Mapping[str, str], key: str, default: float) -> float:
    """Any number, negative too (offsets, angles)."""
    text = environ.get(key, "").strip()
    if not text:
        return default
    try:
        return float(text)
    except ValueError as exc:
        raise ConfigError(f"{key}='{text}' ต้องเป็นตัวเลข") from exc


def _port(environ: Mapping[str, str], key: str, default: int) -> int:
    text = environ.get(key, "").strip()
    if not text:
        return default
    if not text.isdigit() or not 1 <= int(text) <= 65535:
        raise ConfigError(f"{key}='{text}' ต้องเป็นหมายเลขพอร์ต 1–65535")
    return int(text)


def _bool(environ: Mapping[str, str], key: str, default: bool) -> bool:
    text = environ.get(key, "").strip().lower()
    if not text:
        return default
    if text in ("1", "true", "yes", "on"):
        return True
    if text in ("0", "false", "no", "off"):
        return False
    raise ConfigError(f"{key}='{text}' ต้องเป็น true หรือ false")
