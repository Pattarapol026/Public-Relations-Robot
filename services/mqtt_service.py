"""MQTT link to hardware devices — communication test stage only.

This service only moves messages between the software and an MQTT broker. It
knows nothing about the conversation, the UI or the robot adapter, and it
cannot move anything: the only commands it will send are ``SAFE_COMMANDS``.
PING, STATUS and TEST_ECHO are answered without acting; SPEAK asks a device
to say a text (it then reports BUSY until done, then IDLE) and STOP ends what
it is doing. No command moves a motor, wheel or arm.

Being connected to the broker says nothing about any device: a device counts
as heard from only when one of its own messages (a response or telemetry)
arrives, and the robot's status stays with the robot adapter.

Every message is a JSON object (``SCHEMA_VERSION`` 1)::

    {"v": 1, "id": "<uuid>", "ts": "<ISO 8601 UTC>", "type": "command" | "response" | "telemetry" | "status",
     "source": "<client id>", "command": "PING", "payload": {...}, "reply_to": "<id of the command>"}

Topics (``<prefix>`` defaults to ``prrobot/test``): ``<prefix>/command``
(software -> device), ``<prefix>/response`` (device -> software, answers),
``<prefix>/telemetry`` (device -> software, unrequested readings) and
``<prefix>/status/<client id>`` (each client's own online/offline and, for a
device, its work state; retained, and one topic per client because a topic
keeps only one retained message). A device's status payload: ``{"online": bool,
"state": "IDLE" | "BUSY" | "STOPPED", "task": "<command>" | null, "task_id":
"<id>", "result": "done" | "stopped" | null}``; software clients add
``"role": "app"`` and are not counted as devices.
"""
from __future__ import annotations

import json
import logging
import ssl
import threading
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1
SAFE_COMMANDS = frozenset({"PING", "STATUS", "TEST_ECHO", "SPEAK", "STOP"})
MAX_SPEAK_CHARS = 500
# Software clients (this app, test tools) mark their status with this role; any
# other status on the topic is a device's.
APP_ROLE = "app"
MESSAGE_TYPES = frozenset({"command", "response", "telemetry", "status"})
MAX_PAYLOAD_BYTES = 64 * 1024
SEEN_IDS = 512  # message ids remembered to drop duplicates (QoS 1 may deliver twice)


class MqttStatus(Enum):
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    ERROR = "error"


class MessageError(ValueError):
    """A received payload is not a valid message of this schema."""


class MqttError(Exception):
    """A request could not be made or was not answered; the message is safe to show."""


class MqttTimeout(MqttError):
    """The command was sent, but no device answered within the time allowed."""


@dataclass(frozen=True)
class MqttConfig:
    host: str = "127.0.0.1"
    port: int = 1883
    client_id: str = "prrobot-app"
    username: str = ""
    password: str = field(default="", repr=False)
    tls: bool = False
    ca_file: str = ""
    topic_prefix: str = "prrobot/test"
    keepalive_s: int = 30
    response_timeout_s: float = 3.0
    reconnect_min_s: float = 1.0
    reconnect_max_s: float = 30.0

    def topic(self, name: str) -> str:
        return f"{self.topic_prefix.rstrip('/')}/{name}"

    def status_topic(self) -> str:
        """This client's own retained status topic (one per client, so none overwrites another)."""
        return self.topic(f"status/{self.client_id}")


@dataclass(frozen=True)
class Message:
    id: str
    type: str
    ts: str
    source: str
    command: str = ""
    payload: dict = field(default_factory=dict)
    reply_to: str = ""

    def to_json(self) -> bytes:
        data: dict[str, Any] = {
            "v": SCHEMA_VERSION, "id": self.id, "ts": self.ts, "type": self.type,
            "source": self.source, "payload": self.payload,
        }
        if self.command:
            data["command"] = self.command
        if self.reply_to:
            data["reply_to"] = self.reply_to
        return json.dumps(data, ensure_ascii=False).encode("utf-8")


@dataclass(frozen=True)
class StatusSnapshot:
    status: MqttStatus
    since: datetime
    detail: str = ""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def make_message(
    type_: str, source: str, command: str = "", payload: dict | None = None, reply_to: str = ""
) -> Message:
    if type_ not in MESSAGE_TYPES:
        raise ValueError(f"unknown message type {type_!r}")
    return Message(uuid.uuid4().hex, type_, now_iso(), source, command, payload or {}, reply_to)


def parse_message(raw: bytes) -> Message:
    """Validate a received payload. Raises ``MessageError`` saying what is wrong."""
    if len(raw) > MAX_PAYLOAD_BYTES:
        raise MessageError(f"payload too large ({len(raw)} bytes)")
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MessageError(f"not JSON: {exc}") from None
    if not isinstance(data, dict):
        raise MessageError("not a JSON object")
    if data.get("v") != SCHEMA_VERSION:
        raise MessageError(f"unsupported schema version {data.get('v')!r}")
    for key in ("id", "ts", "type", "source"):
        if not isinstance(data.get(key), str) or not data[key]:
            raise MessageError(f"missing or empty {key!r}")
    if data["type"] not in MESSAGE_TYPES:
        raise MessageError(f"unknown type {data['type']!r}")
    payload = data.get("payload", {})
    if not isinstance(payload, dict):
        raise MessageError("payload is not an object")
    command = data.get("command", "")
    reply_to = data.get("reply_to", "")
    if not isinstance(command, str) or not isinstance(reply_to, str):
        raise MessageError("command and reply_to must be strings")
    if data["type"] in ("command", "response") and not command:
        raise MessageError(f"a {data['type']} needs a command")
    if data["type"] == "response" and not reply_to:
        raise MessageError("a response needs reply_to")
    return Message(data["id"], data["type"], data["ts"], data["source"], command, payload, reply_to)


class MqttService:
    """Connects in the background, reconnects by itself, and never raises from callbacks.

    Listeners (``on_status``, ``on_message``) are called on the MQTT network
    thread; a Qt user must hand them over to the GUI thread.
    """

    def __init__(self, config: MqttConfig) -> None:
        self._config = config
        self._lock = threading.Lock()
        self._status = StatusSnapshot(MqttStatus.DISCONNECTED, datetime.now(timezone.utc))
        self._client = None
        self._stopping = False
        self._pending: dict[str, tuple[threading.Event, list[Message]]] = {}
        self._seen: OrderedDict[str, None] = OrderedDict()
        self.on_status: list[Callable[[StatusSnapshot], None]] = []
        self.on_message: list[Callable[[Message], None]] = []
        self.counters = {"received": 0, "malformed": 0, "duplicates": 0, "sent": 0}
        self.last_error = ""
        self.last_device_message: Message | None = None
        # Latest status payload of each device, by its client id (from <prefix>/status).
        self.device_states: dict[str, dict] = {}

    # --- state ---

    @property
    def config(self) -> MqttConfig:
        return self._config

    @property
    def status(self) -> StatusSnapshot:
        return self._status

    @property
    def is_connected(self) -> bool:
        return self._status.status is MqttStatus.CONNECTED

    # --- lifecycle ---

    def start(self) -> None:
        """Begin connecting (returns at once; watch ``status``)."""
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
        if config.tls:
            client.tls_set(ca_certs=config.ca_file or None, cert_reqs=ssl.CERT_REQUIRED)
        client.reconnect_delay_set(
            min_delay=max(1, int(config.reconnect_min_s)), max_delay=max(1, int(config.reconnect_max_s))
        )
        offline = make_message("status", config.client_id, payload={"online": False, "role": APP_ROLE})
        client.will_set(config.status_topic(), offline.to_json(), qos=1, retain=True)
        client.on_connect = self._on_connect
        client.on_subscribe = self._on_subscribe
        client.on_disconnect = self._on_disconnect
        client.on_message = self._on_message
        client.on_connect_fail = self._on_connect_fail
        client.enable_logger(logging.getLogger("paho.mqtt"))  # packet-level DEBUG, off at the app's INFO
        self._client = client
        self._set_status(MqttStatus.CONNECTING, f"{config.host}:{config.port}")
        try:
            client.connect_async(config.host, config.port, keepalive=config.keepalive_s)
            client.loop_start()
        except Exception as exc:  # bad host name, TLS file missing...
            logger.exception("MQTT could not start")
            self._set_status(MqttStatus.ERROR, str(exc))

    def stop(self) -> None:
        """Say goodbye, disconnect and stop the network thread. Safe to call twice."""
        client, self._client = self._client, None
        if client is None:
            return
        self._stopping = True
        try:
            if self.is_connected:
                offline = make_message("status", self._config.client_id, payload={"online": False, "role": APP_ROLE})
                client.publish(self._config.status_topic(), offline.to_json(), qos=1, retain=True).wait_for_publish(1.0)
            client.disconnect()
        except Exception:
            logger.exception("MQTT disconnect failed")
        finally:
            client.loop_stop()
        with self._lock:
            for event, _replies in self._pending.values():
                event.set()
        self._set_status(MqttStatus.DISCONNECTED, "stopped")

    # --- sending ---

    def publish(self, topic_name: str, message: Message, qos: int = 1, retain: bool = False) -> None:
        """Publish under ``<prefix>/<topic_name>``. Raises ``MqttError`` when not connected."""
        client = self._client
        if client is None or not self.is_connected:
            raise MqttError("ยังไม่ได้เชื่อมต่อ MQTT broker")
        info = client.publish(self._config.topic(topic_name), message.to_json(), qos=qos, retain=retain)
        if info.rc != 0:
            raise MqttError(f"ส่งข้อความ MQTT ไม่สำเร็จ (rc={info.rc})")
        self.counters["sent"] += 1

    def publish_raw(self, topic_name: str, raw: bytes) -> None:
        """Publish bytes as they are (tests send malformed messages with it). Raises ``MqttError``."""
        client = self._client
        if client is None or not self.is_connected:
            raise MqttError("ยังไม่ได้เชื่อมต่อ MQTT broker")
        client.publish(self._config.topic(topic_name), raw, qos=1).wait_for_publish(2.0)

    def request(self, command: str, payload: dict | None = None, timeout_s: float | None = None) -> Message:
        """Send a safe command and wait for the device's response. Raises ``MqttError``."""
        command = command.strip().upper()
        if command not in SAFE_COMMANDS:
            raise MqttError(f"คำสั่ง {command!r} ไม่อนุญาตในขั้นทดสอบการสื่อสาร (ใช้ได้: {', '.join(sorted(SAFE_COMMANDS))})")
        if command == "SPEAK":
            text = (payload or {}).get("text")
            if not isinstance(text, str) or not text.strip() or len(text) > MAX_SPEAK_CHARS:
                raise MqttError(f"SPEAK ต้องมีข้อความ (payload.text) ยาวไม่เกิน {MAX_SPEAK_CHARS} ตัวอักษร")
        message = make_message("command", self._config.client_id, command, payload)
        event, replies = threading.Event(), []
        with self._lock:
            self._pending[message.id] = (event, replies)
        try:
            self.publish("command", message)
            if not event.wait(self._config.response_timeout_s if timeout_s is None else timeout_s):
                raise MqttTimeout(f"ไม่มีอุปกรณ์ตอบ {command} ภายในเวลาที่กำหนด")
        finally:
            with self._lock:
                self._pending.pop(message.id, None)
        if not replies:
            raise MqttError("หยุดการเชื่อมต่อ MQTT ก่อนได้รับคำตอบ")
        return replies[0]

    # --- paho callbacks (network thread) ---

    def _on_connect(self, client, _userdata, _flags, reason_code, _properties) -> None:
        if reason_code.is_failure:
            self._set_status(MqttStatus.ERROR, f"broker ปฏิเสธการเชื่อมต่อ: {reason_code}")
            return
        config = self._config
        client.subscribe([(config.topic("response"), 1), (config.topic("telemetry"), 1), (config.topic("status/#"), 1)])
        online = make_message("status", config.client_id, payload={"online": True, "role": APP_ROLE})
        client.publish(config.status_topic(), online.to_json(), qos=1, retain=True)

    def _on_subscribe(self, _client, _userdata, _mid, reason_codes, _properties) -> None:
        # Connected for our purposes only now: a command sent before the response
        # topic is subscribed could have its answer missed.
        if any(code.is_failure for code in reason_codes):
            self._set_status(MqttStatus.ERROR, "broker ไม่ยอมให้ subscribe topic ของการทดสอบ")
            return
        self._set_status(MqttStatus.CONNECTED, f"{self._config.host}:{self._config.port}")

    def _on_disconnect(self, _client, _userdata, _flags, reason_code, _properties) -> None:
        if self._stopping:
            return
        # paho reconnects by itself (reconnect_delay_set); say so instead of "error".
        self._set_status(MqttStatus.CONNECTING, f"การเชื่อมต่อขาด ({reason_code}) กำลังเชื่อมต่อใหม่")

    def _on_connect_fail(self, _client, _userdata) -> None:
        if not self._stopping:
            self._set_status(
                MqttStatus.ERROR,
                f"เชื่อมต่อ broker {self._config.host}:{self._config.port} ไม่ได้ จะลองใหม่อัตโนมัติ",
            )

    def _on_message(self, _client, _userdata, mqtt_message) -> None:
        try:
            message = parse_message(mqtt_message.payload)
        except MessageError as exc:
            self.counters["malformed"] += 1
            self.last_error = f"{mqtt_message.topic}: {exc}"
            logger.warning("MQTT malformed message on %s: %s", mqtt_message.topic, exc)
            return
        if message.source == self._config.client_id:
            return  # our own status echo
        if message.type == "status" and message.payload.get("role") == APP_ROLE:
            return  # another software client (a second app, a test tool): not a device
        with self._lock:
            if message.id in self._seen:
                self.counters["duplicates"] += 1
                return
            self._seen[message.id] = None
            while len(self._seen) > SEEN_IDS:
                self._seen.popitem(last=False)
            pending = self._pending.get(message.reply_to) if message.type == "response" else None
        self.counters["received"] += 1
        self.last_device_message = message
        if message.type == "status":
            self.device_states[message.source] = dict(message.payload)
        if pending is not None:
            pending[1].append(message)
            pending[0].set()
        for listener in list(self.on_message):
            try:
                listener(message)
            except Exception:
                logger.exception("MQTT message listener failed")

    def _set_status(self, status: MqttStatus, detail: str = "") -> None:
        snapshot = StatusSnapshot(status, datetime.now(timezone.utc), detail)
        changed = (status, detail) != (self._status.status, self._status.detail)
        self._status = snapshot
        if not changed:
            return
        logger.info("MQTT status: %s %s", status.value, detail)
        for listener in list(self.on_status):
            try:
                listener(snapshot)
            except Exception:
                logger.exception("MQTT status listener failed")


def wait_for(condition: Callable[[], bool], timeout_s: float) -> bool:
    """Poll ``condition`` until it holds or the time is up (for tools and tests)."""
    deadline = time.monotonic() + timeout_s
    while not condition():
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.02)
    return True
