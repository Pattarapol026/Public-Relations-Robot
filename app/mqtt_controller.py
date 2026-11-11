"""Hands the MQTT service's news to the GUI thread, and runs the Admin's test commands.

Separate from the robot and the conversation. Commands go through the one
MqttService connection, one at a time, on the thread pool: the GUI never waits
for a device. A command counts as answered only when the device's response
to that very command (``reply_to``) has arrived.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from enum import Enum

from PySide6.QtCore import QObject, QThreadPool, Signal, Slot

from app.workers import FunctionWorker
from config.settings import Settings
from services.mqtt_service import (
    Message,
    MqttConfig,
    MqttError,
    MqttService,
    MqttStatus,
    MqttTimeout,
    StatusSnapshot,
)

logger = logging.getLogger(__name__)

# The Admin's test buttons talk only to a broker on this computer.
LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
PANEL_COMMANDS = ("PING", "SPEAK", "STOP")


class Outcome(Enum):
    PENDING = "pending"  # sent, no answer yet
    OK = "ok"  # the device answered and did it
    REFUSED = "refused"  # the device answered and refused (ok false, e.g. busy)
    TIMEOUT = "timeout"  # sent, nobody answered in time
    FAILED = "failed"  # could not be sent (not connected, invalid input)


@dataclass(frozen=True)
class CommandRecord:
    command: str
    payload: dict
    sent_at: datetime
    outcome: Outcome = Outcome.PENDING
    reply: Message | None = None
    received_at: datetime | None = None
    round_trip_ms: float | None = None
    detail: str = ""

    @property
    def from_simulator(self) -> bool:
        return bool(self.reply is not None and self.reply.payload.get("simulated"))


def mqtt_config(settings: Settings) -> MqttConfig:
    return MqttConfig(
        host=settings.mqtt_host,
        port=settings.mqtt_port,
        client_id=settings.mqtt_client_id,
        username=settings.mqtt_username,
        password=settings.mqtt_password,
        tls=settings.mqtt_tls,
        ca_file=settings.mqtt_ca_file,
        topic_prefix=settings.mqtt_topic_prefix,
        response_timeout_s=settings.mqtt_response_timeout_s,
    )


class MqttController(QObject):
    # Emitted from the MQTT network thread; Qt queues them to the receivers' (GUI) thread.
    status_changed = Signal(object)  # StatusSnapshot
    message_received = Signal(object)  # Message from a device (response, telemetry, status)
    command_changed = Signal(object)  # CommandRecord: sent (PENDING), then its outcome

    def __init__(self, service: MqttService, pool: QThreadPool | None = None, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._service = service
        self._pool = pool or QThreadPool.globalInstance()
        self._last: CommandRecord | None = None
        self._token = 0
        service.on_status.append(self.status_changed.emit)
        service.on_message.append(self.message_received.emit)

    @property
    def service(self) -> MqttService:
        return self._service

    @property
    def status(self) -> StatusSnapshot:
        return self._service.status

    @property
    def last_device_message(self) -> Message | None:
        return self._service.last_device_message

    @property
    def last_command(self) -> CommandRecord | None:
        return self._last

    @property
    def busy(self) -> bool:
        """A command is on its way: the next one waits (no double sends from quick clicks)."""
        return self._last is not None and self._last.outcome is Outcome.PENDING

    @property
    def local_broker(self) -> bool:
        return self._service.config.host.strip().lower() in LOCAL_HOSTS

    def device_status(self) -> tuple[str, dict | None]:
        """("online" | "offline" | "unknown", latest status payload) of the device last heard from.

        "unknown" until a device has published a status: being connected to the
        broker says nothing about any device.
        """
        states = self._service.device_states
        if not states or not self._service.is_connected:
            return "unknown", None  # without the broker, nothing known now about any device
        last = self._service.last_device_message
        source = last.source if last is not None and last.source in states else next(reversed(states))
        state = states[source]
        return ("online" if state.get("online") else "offline"), {"source": source, **state}

    def can_send(self) -> str:
        """"" when a test command may be sent now, else why not (for the Admin)."""
        if not self.local_broker:
            return f"ปุ่มทดสอบใช้ได้เฉพาะ broker ในเครื่อง (ตอนนี้ตั้งเป็น {self._service.config.host})"
        if self._service.status.status is not MqttStatus.CONNECTED:
            return "ยังไม่ได้เชื่อมต่อ MQTT broker"
        if self.busy:
            return f"รอคำตอบของ {self._last.command} อยู่"
        return ""

    def send(self, command: str, payload: dict | None = None) -> CommandRecord:
        """Send a test command in the background; ``command_changed`` reports its outcome."""
        command = command.strip().upper()
        payload = dict(payload or {})
        record = CommandRecord(command, payload, datetime.now(timezone.utc))
        reason = "" if command in PANEL_COMMANDS else f"ปุ่มทดสอบไม่มีคำสั่ง {command}"
        reason = reason or self.can_send()
        if reason:
            record = replace(record, outcome=Outcome.FAILED, detail=reason)
            if not self.busy:  # never overwrite the command still waiting for its answer
                self._last = record
            self.command_changed.emit(record)
            return record
        self._token += 1
        self._last = record
        logger.info("MQTT test command %s sent from the Admin dashboard", command)
        self.command_changed.emit(record)
        worker = FunctionWorker(self._token, self._request, record)
        worker.signals.finished.connect(self._on_answered)
        self._pool.start(worker)
        return record

    def start(self) -> None:
        self._service.start()

    def shutdown(self) -> None:
        self._token += 1  # an answer still on its way is no longer shown
        self._service.stop()

    # --- internals ---

    def _request(self, record: CommandRecord) -> CommandRecord:
        """Worker thread: wait for the device's answer to this command. Never raises."""
        started = time.monotonic()
        try:
            reply = self._service.request(record.command, record.payload)
        except MqttTimeout as exc:
            return replace(record, outcome=Outcome.TIMEOUT, detail=str(exc))
        except MqttError as exc:
            return replace(record, outcome=Outcome.FAILED, detail=str(exc))
        except Exception as exc:  # noqa: BLE001 - shown, never crashes the Admin
            logger.exception("MQTT test command failed")
            return replace(record, outcome=Outcome.FAILED, detail=f"{type(exc).__name__}: {exc}")
        ok = reply.payload.get("ok") is not False
        return replace(
            record,
            outcome=Outcome.OK if ok else Outcome.REFUSED,
            reply=reply,
            received_at=datetime.now(timezone.utc),
            round_trip_ms=round((time.monotonic() - started) * 1000, 1),
            detail="" if ok else str(reply.payload.get("error", "refused")),
        )

    @Slot(object, object)
    def _on_answered(self, token: int, record: CommandRecord) -> None:
        if token != self._token:
            return
        self._last = record
        logger.info("MQTT test command %s: %s %s", record.command, record.outcome.value, record.detail)
        self.command_changed.emit(record)
