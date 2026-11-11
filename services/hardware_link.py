"""MQTT link to the real robot hardware: direction commands sent by the Admin, and STOP.

The Admin may also send a message typed by hand (``send_text``, e.g. "ไปห้องกิจกรรมหน่อย"),
exactly as typed, under the same rules as a direction, and listen to topics of
their choice; every message that arrives is passed to ``on_message``.

The latest message on ``status_topic`` is kept as the robot's own word about
itself, marked when it is only the broker's retained copy.

Separate from ``mqtt_service`` (the JSON communication test against a broker on
this computer). The hardware speaks plain text on its own topics, for example
``left 50`` on ``robot/direction``, so this link sends exactly that and nothing
else: only the directions in ``DIRECTIONS`` (turns take an angle in degrees, moves
a distance in centimetres), only values in the configured range,
at most one direction command per ``min_interval_s``, and never before a STOP
message is configured, because a robot that can be started must be stoppable.

What is known about a command is kept apart: the broker acknowledging it (QoS 1)
only says the message reached the broker. The hardware received it only if it
answers on ``reply_topic`` within ``reply_window_s``; without a reply topic that
cannot be known, and the record says so.
"""
from __future__ import annotations

import logging
import re
import threading
import time
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Callable

from services.mqtt_service import MqttStatus, StatusSnapshot

logger = logging.getLogger(__name__)

TURNS = ("left", "right")  # the value is an angle in degrees
MOVES = ("forward", "backward")  # the value is a distance in centimetres
DIRECTIONS = TURNS + MOVES
KIND_DIRECTION = "direction"
KIND_STOP = "stop"
KIND_TEXT = "text"  # a message the Admin typed, sent as it is
KIND_ARM = "arm"  # an arm message (wave, back to rest)
MAX_REPLY_CHARS = 300
MAX_TEXT_CHARS = 200
MAX_LISTEN_TOPICS = 10
_TOPIC = re.compile(r"[A-Za-z0-9._/\-]{1,100}")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


class HardwareLinkError(Exception):
    """A command was not sent; the message is safe to show."""


@dataclass(frozen=True)
class HardwareLinkConfig:
    host: str
    port: int = 1883
    client_id: str = "prrobot-app-hw"
    username: str = ""
    password: str = field(default="", repr=False)
    direction_topic: str = "robot/direction"
    stop_payload: str = ""  # "" = not known yet: direction commands stay off
    reply_topic: str = ""  # "" = the hardware does not answer, so receipt cannot be confirmed
    status_topic: str = "robot/r1/status"  # what the robot says about itself; "" = not watched
    arm_topic: str = "robot/arm"
    arm_wave_payload: str = "wave"
    arm_home_payload: str = "home"
    payload_format: str = "{direction} {value}"
    turn_max_deg: int = 180  # a turn is 1..turn_max_deg degrees
    move_max_cm: int = 100  # a move is 1..move_max_cm centimetres
    min_interval_s: float = 1.0
    reply_window_s: float = 3.0
    keepalive_s: int = 30


@dataclass(frozen=True)
class SentCommand:
    kind: str  # KIND_DIRECTION | KIND_STOP
    payload: str
    topic: str
    sent_at: datetime
    broker_ack_at: datetime | None = None
    reply: str = ""
    reply_at: datetime | None = None

    @property
    def acked(self) -> bool:
        return self.broker_ack_at is not None


@dataclass(frozen=True)
class StatusMessage:
    """The latest message on the robot's status topic."""

    text: str
    received_at: datetime
    # True = the broker's stored (retained) copy, handed over on subscribing:
    # it says nothing about when the robot sent it or whether it is still on.
    retained: bool


@dataclass(frozen=True)
class ReceivedMessage:
    """Any message that arrives: on the status or reply topic, or one the Admin listens to."""

    topic: str
    text: str
    received_at: datetime
    retained: bool  # the broker's stored copy, not something said just now


def listen_refusal(topic: str) -> str:
    """"" if ``topic`` may be listened to: a topic, with ``+`` for one level or ``#`` at the end."""
    if not topic or len(topic) > 100:
        return "topic ที่จะฟังต้องยาว 1–100 ตัวอักษร"
    levels = topic.split("/")
    for index, level in enumerate(levels):
        if level in ("+", "#"):
            if level == "#" and index != len(levels) - 1:
                return "# ใช้ได้เฉพาะท้ายสุด เช่น robot/#"
            continue
        if not _TOPIC.fullmatch(level) or "/" in level:
            return "topic ใช้ได้เฉพาะ a-z A-Z 0-9 . _ - / และ + หรือ # แทนทั้งชั้น (เช่น robot/+/status, robot/#)"
    return ""


def topic_matches(pattern: str, topic: str) -> bool:
    """MQTT matching: ``+`` is one level, ``#`` the rest."""
    wanted, given = pattern.split("/"), topic.split("/")
    for index, level in enumerate(wanted):
        if level == "#":
            return True
        if index >= len(given) or (level != "+" and level != given[index]):
            return False
    return len(wanted) == len(given)


def format_payload(config: HardwareLinkConfig, direction: str, value: int) -> str:
    return config.payload_format.format(direction=direction, value=value)


def value_range(config: HardwareLinkConfig, direction: str) -> tuple[int, int]:
    return (1, config.turn_max_deg) if direction in TURNS else (1, config.move_max_cm)


def unit(direction: str) -> str:
    return "องศา" if direction in TURNS else "ซม."


def _matches(wanted: str, topic: str) -> bool:
    """``wanted`` is a topic, or ends in ``#`` for everything below it; "" matches nothing."""
    if not wanted:
        return False
    if wanted.endswith("#"):
        return topic.startswith(wanted[:-1])
    return topic == wanted


class HardwareLink:
    """Connects in the background and reconnects by itself; listeners run on the MQTT thread."""

    def __init__(self, config: HardwareLinkConfig, clock: Callable[[], float] = time.monotonic) -> None:
        self._config = config
        self._clock = clock
        self._lock = threading.Lock()
        self._client = None
        self._stopping = False
        self._status = StatusSnapshot(MqttStatus.DISCONNECTED, datetime.now(timezone.utc))
        self._last_direction_at = float("-inf")
        self._last_arm_at = float("-inf")
        self._last: SentCommand | None = None
        self._last_mid: int | None = None
        self._early_acks: set[int] = set()  # PUBACKs that arrived before publish() returned
        self.last_reply: tuple[str, datetime] | None = None
        self.robot_status: StatusMessage | None = None
        self._listening: list[str] = []  # topics the Admin listens to, besides status and reply
        self.on_status: list[Callable[[StatusSnapshot], None]] = []
        self.on_change: list[Callable[[SentCommand], None]] = []
        self.on_robot_status: list[Callable[[StatusMessage], None]] = []
        self.on_message: list[Callable[[ReceivedMessage], None]] = []  # every message that arrives

    # --- state ---

    @property
    def config(self) -> HardwareLinkConfig:
        return self._config

    @property
    def status(self) -> StatusSnapshot:
        return self._status

    @property
    def is_connected(self) -> bool:
        return self._status.status is MqttStatus.CONNECTED

    @property
    def last_command(self) -> SentCommand | None:
        return self._last

    def topic_refusal(self, topic: str) -> str:
        """"" if a direction command may be published on ``topic``."""
        config = self._config
        if not _TOPIC.fullmatch(topic):
            return "topic ใช้ได้เฉพาะ a-z A-Z 0-9 . _ - / ยาวไม่เกิน 100 และไม่มี # หรือ +"
        if topic == config.status_topic or _matches(config.reply_topic, topic):
            return f"{topic} เป็น topic ที่ใช้รับข้อความจากหุ่นยนต์ จึงส่งคำสั่งทิศทางไปไม่ได้"
        return ""

    def seconds_until_next_direction(self) -> float:
        return max(0.0, self._last_direction_at + self._config.min_interval_s - self._clock())

    def direction_refusal(self, direction: str, value: int) -> str:
        """"" if this direction command may be sent now, else why not."""
        config = self._config
        if not self.is_connected:
            return "ยังไม่ได้เชื่อมต่อ broker ของฮาร์ดแวร์"
        if not config.stop_payload:
            return "ยังไม่ได้ตั้งคำสั่งหยุด (MQTT_HW_STOP_PAYLOAD) จึงยังไม่เปิดให้สั่งทิศทาง"
        if direction not in DIRECTIONS:
            return f"ไม่มีทิศทาง {direction!r} (ใช้ได้: {', '.join(DIRECTIONS)})"
        low, high = value_range(config, direction)
        if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
            what = "มุม" if direction in TURNS else "ระยะ"
            return f"{what}ต้องเป็นจำนวนเต็ม {low}–{high} {unit(direction)}"
        wait = self.seconds_until_next_direction()
        if wait > 0:
            return f"รออีก {wait:.1f} วินาทีก่อนส่งคำสั่งถัดไป"
        return ""

    def text_refusal(self, text: str) -> str:
        """"" if this typed message may be sent now. Same rules as a direction: the
        hardware may move on it, so it needs a STOP message and the pause between commands."""
        if not self.is_connected:
            return "ยังไม่ได้เชื่อมต่อ broker ของฮาร์ดแวร์"
        if not self._config.stop_payload:
            return "ยังไม่ได้ตั้งคำสั่งหยุด (MQTT_HW_STOP_PAYLOAD) จึงยังไม่เปิดให้ส่งข้อความ"
        if not text.strip():
            return "พิมพ์ข้อความที่จะส่งก่อน"
        if len(text) > MAX_TEXT_CHARS:
            return f"ข้อความยาวได้ไม่เกิน {MAX_TEXT_CHARS} ตัวอักษร"
        if _CONTROL.search(text):
            return "ข้อความต้องเป็นบรรทัดเดียว"
        wait = self.seconds_until_next_direction()
        if wait > 0:
            return f"รออีก {wait:.1f} วินาทีก่อนส่งคำสั่งถัดไป"
        return ""

    def arm_refusal(self, payload: str, topic: str, moving: bool = True) -> str:
        """"" if this arm message may be sent now. ``moving``: it raises or moves the arm (the pause between commands
        applies); a message that sends it back to rest is not held back, like STOP."""
        if not self.is_connected:
            return "ยังไม่ได้เชื่อมต่อ broker ของฮาร์ดแวร์"
        if not self._config.stop_payload:
            return "ยังไม่ได้ตั้งคำสั่งหยุด (MQTT_HW_STOP_PAYLOAD) จึงยังไม่เปิดให้สั่งแขน"
        if not payload.strip():
            return "ยังไม่ได้ตั้งข้อความสั่งแขน"
        if len(payload) > MAX_TEXT_CHARS or _CONTROL.search(payload):
            return "ข้อความสั่งแขนต้องเป็นบรรทัดเดียว"
        reason = self.topic_refusal(topic)
        if reason:
            return reason
        wait = self._last_arm_at + self._config.min_interval_s - self._clock() if moving else 0.0
        if wait > 0:
            return f"รออีก {wait:.1f} วินาทีก่อนสั่งแขนอีกครั้ง"
        return ""

    @property
    def listening(self) -> tuple[str, ...]:
        return tuple(self._listening)

    def listen(self, topic: str) -> str:
        """Also receive messages on ``topic`` (from now, and after each reconnect); "" or why not."""
        topic = topic.strip()
        reason = listen_refusal(topic)
        if reason:
            return reason
        if topic in self._listening:
            return ""
        if len(self._listening) >= MAX_LISTEN_TOPICS:
            return f"ฟังได้ไม่เกิน {MAX_LISTEN_TOPICS} topic"
        self._listening.append(topic)
        client = self._client
        if client is not None and self.is_connected:
            client.subscribe(topic, qos=1)
        return ""

    def unlisten(self, topic: str) -> None:
        if topic not in self._listening:
            return
        self._listening.remove(topic)
        client = self._client
        if client is not None and self.is_connected and topic not in self._own_topics():
            client.unsubscribe(topic)

    def _own_topics(self) -> set[str]:
        return {self._config.reply_topic, self._config.status_topic} - {""}

    def stop_refusal(self) -> str:
        if not self._config.stop_payload:
            return "ยังไม่ได้ตั้งคำสั่งหยุด (MQTT_HW_STOP_PAYLOAD)"
        if not self.is_connected:
            return "ยังไม่ได้เชื่อมต่อ broker ของฮาร์ดแวร์"
        return ""

    # --- lifecycle ---

    def start(self) -> None:
        import paho.mqtt.client as mqtt

        if self._client is not None:
            return
        config = self._config
        self._stopping = False
        client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2, client_id=config.client_id,
            protocol=mqtt.MQTTv311, clean_session=True,
        )
        if config.username:
            client.username_pw_set(config.username, config.password or None)
        client.reconnect_delay_set(min_delay=1, max_delay=30)
        client.on_connect = self._on_connect
        client.on_subscribe = self._on_subscribe
        client.on_disconnect = self._on_disconnect
        client.on_connect_fail = self._on_connect_fail
        client.on_publish = self._on_publish
        client.on_message = self._on_message
        self._client = client
        self._set_status(MqttStatus.CONNECTING, f"{config.host}:{config.port}")
        try:
            client.connect_async(config.host, config.port, keepalive=config.keepalive_s)
            client.loop_start()
        except Exception as exc:
            logger.exception("Hardware MQTT link could not start")
            self._set_status(MqttStatus.ERROR, str(exc))

    def stop(self) -> None:
        """Disconnect and stop the network thread. Sends nothing to the hardware. Safe to call twice."""
        client, self._client = self._client, None
        if client is None:
            return
        self._stopping = True
        try:
            client.disconnect()
        except Exception:
            logger.exception("Hardware MQTT disconnect failed")
        finally:
            client.loop_stop()
        self._set_status(MqttStatus.DISCONNECTED, "stopped")

    # --- sending ---

    def send_direction(self, direction: str, value: int, topic: str = "") -> SentCommand:
        """To ``topic`` (default: the configured direction topic)."""
        topic = topic or self._config.direction_topic
        reason = self.direction_refusal(direction, value) or self.topic_refusal(topic)
        if reason:
            raise HardwareLinkError(reason)
        self._last_direction_at = self._clock()
        return self._publish(KIND_DIRECTION, format_payload(self._config, direction, value), topic)

    def send_text(self, text: str, topic: str) -> SentCommand:
        """``text`` exactly as typed (UTF-8), to ``topic``."""
        reason = self.text_refusal(text) or self.topic_refusal(topic)
        if reason:
            raise HardwareLinkError(reason)
        self._last_direction_at = self._clock()
        return self._publish(KIND_TEXT, text, topic)

    def send_arm(self, payload: str, topic: str, moving: bool = True) -> SentCommand:
        """An arm message, exactly as given, to ``topic``. Does not count against the pause between directions."""
        reason = self.arm_refusal(payload, topic, moving)
        if reason:
            raise HardwareLinkError(reason)
        if moving:
            self._last_arm_at = self._clock()
        return self._publish(KIND_ARM, payload, topic)

    def send_stop(self, topic: str = "") -> SentCommand:
        """Never held back by the interval between direction commands. To ``topic`` (default:
        the direction topic); a topic the robot talks on is never used."""
        reason = self.stop_refusal()
        if reason:
            raise HardwareLinkError(reason)
        if topic and self.topic_refusal(topic):
            topic = ""
        return self._publish(KIND_STOP, self._config.stop_payload, topic)

    def _publish(self, kind: str, payload: str, topic: str = "") -> SentCommand:
        client = self._client
        if client is None:
            raise HardwareLinkError("ยังไม่ได้เชื่อมต่อ broker ของฮาร์ดแวร์")
        topic = topic or self._config.direction_topic
        info = client.publish(topic, payload.encode("utf-8"), qos=1, retain=False)
        if info.rc != 0:
            raise HardwareLinkError(f"ส่งข้อความไม่สำเร็จ (rc={info.rc})")
        record = SentCommand(kind, payload, topic, datetime.now(timezone.utc))
        with self._lock:
            if info.mid in self._early_acks:
                self._early_acks.discard(info.mid)
                record = replace(record, broker_ack_at=datetime.now(timezone.utc))
            self._last, self._last_mid = record, info.mid
        logger.info("Hardware command sent: topic=%s payload=%r", topic, payload)
        self._notify(record)
        return record

    # --- paho callbacks (network thread) ---

    def _on_connect(self, client, _userdata, _flags, reason_code, _properties) -> None:
        if reason_code.is_failure:
            self._set_status(MqttStatus.ERROR, f"broker ปฏิเสธการเชื่อมต่อ: {reason_code}")
            return
        topics = self._own_topics() | set(self._listening)
        if topics:
            client.subscribe([(topic, 1) for topic in sorted(topics)])
        else:
            self._set_status(MqttStatus.CONNECTED, f"{self._config.host}:{self._config.port}")

    def _on_subscribe(self, _client, _userdata, _mid, reason_codes, _properties) -> None:
        if any(code.is_failure for code in reason_codes):
            if self.is_connected:  # a topic the Admin added later: the link itself is fine
                logger.warning("Broker refused a topic the Admin listens to")
                return
            self._set_status(MqttStatus.ERROR, "broker ไม่ยอมให้ subscribe topic ของหุ่นยนต์")
            return
        self._set_status(MqttStatus.CONNECTED, f"{self._config.host}:{self._config.port}")

    def _on_disconnect(self, _client, _userdata, _flags, reason_code, _properties) -> None:
        if not self._stopping:
            self._set_status(MqttStatus.CONNECTING, f"การเชื่อมต่อขาด ({reason_code}) กำลังเชื่อมต่อใหม่")

    def _on_connect_fail(self, _client, _userdata) -> None:
        if not self._stopping:
            self._set_status(
                MqttStatus.ERROR,
                f"เชื่อมต่อ {self._config.host}:{self._config.port} ไม่ได้ จะลองใหม่อัตโนมัติ",
            )

    def _on_publish(self, _client, _userdata, mid, _reason_code, _properties) -> None:
        with self._lock:
            if mid != self._last_mid or self._last is None:
                if len(self._early_acks) > 64:  # acks of older commands nobody waits for
                    self._early_acks.clear()
                self._early_acks.add(mid)
                return
            record = self._last = replace(self._last, broker_ack_at=datetime.now(timezone.utc))
        self._notify(record)

    def _on_message(self, _client, _userdata, mqtt_message) -> None:
        text = mqtt_message.payload.decode("utf-8", errors="replace")[:MAX_REPLY_CHARS]
        now = datetime.now(timezone.utc)
        received = ReceivedMessage(mqtt_message.topic, text, now, bool(mqtt_message.retain))
        for listener in list(self.on_message):
            try:
                listener(received)
            except Exception:
                logger.exception("Hardware link listener failed")
        if self._config.status_topic and mqtt_message.topic == self._config.status_topic:
            self.robot_status = StatusMessage(text, now, bool(mqtt_message.retain))
            logger.info("Robot status on %s: %r%s", mqtt_message.topic, text,
                        " (retained)" if mqtt_message.retain else "")
            for listener in list(self.on_robot_status):
                try:
                    listener(self.robot_status)
                except Exception:
                    logger.exception("Hardware link listener failed")
        if not _matches(self._config.reply_topic, mqtt_message.topic) or mqtt_message.retain:
            return  # a stored copy from before is not an answer to what was just sent
        self.last_reply = (text, now)
        record = None
        with self._lock:
            last = self._last
            if (last is not None and not last.reply
                    and (now - last.sent_at).total_seconds() <= self._config.reply_window_s):
                record = self._last = replace(last, reply=text, reply_at=now)
        logger.info("Hardware reply on %s: %r", mqtt_message.topic, text)
        if record is not None:
            self._notify(record)

    def _notify(self, record: SentCommand) -> None:
        for listener in list(self.on_change):
            try:
                listener(record)
            except Exception:
                logger.exception("Hardware link listener failed")

    def _set_status(self, status: MqttStatus, detail: str = "") -> None:
        changed = (status, detail) != (self._status.status, self._status.detail)
        self._status = StatusSnapshot(status, datetime.now(timezone.utc), detail)
        if not changed:
            return
        logger.info("Hardware MQTT status: %s %s", status.value, detail)
        for listener in list(self.on_status):
            try:
                listener(self._status)
            except Exception:
                logger.exception("Hardware link status listener failed")
