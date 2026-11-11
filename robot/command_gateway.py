"""The single checkpoint every robot command passes before reaching an adapter.

Voice, touch, Admin and (later) AI-suggested intents all submit a
``CommandRequest`` here. Nothing is trusted: the command type, the target and
the robot's readiness are verified on every call.
"""
from __future__ import annotations

import concurrent.futures
import logging
import threading
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Iterable

from config.settings import ID_PATTERN
from robot.interfaces import ActionOutcome, RobotAdapter, RobotConnectionError, RobotState

logger = logging.getLogger(__name__)


class CommandType(str, Enum):
    ACTION = "action"


class CommandStatus(str, Enum):
    SUCCEEDED = "succeeded"
    REJECTED = "rejected"
    FAILED = "failed"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class CommandRequest:
    # ``type`` and ``target`` are deliberately loose: callers may pass anything
    # (including model output) and the gateway decides whether it is valid.
    type: object
    target: object
    source: str = "touch"
    command_id: str = field(default_factory=lambda: uuid.uuid4().hex)


@dataclass(frozen=True)
class CommandResult:
    command_id: str
    status: CommandStatus
    code: str
    message: str

    @property
    def ok(self) -> bool:
        return self.status is CommandStatus.SUCCEEDED


# code -> (status, user-facing message)
_OUTCOMES: dict[str, tuple[CommandStatus, str]] = {
    "ok": (CommandStatus.SUCCEEDED, "หุ่นยนต์ทำงานตามคำสั่งเรียบร้อยแล้ว"),
    "unknown_command": (CommandStatus.REJECTED, "ไม่รู้จักคำสั่งนี้"),
    "invalid_parameter": (CommandStatus.REJECTED, "รูปแบบคำสั่งไม่ถูกต้อง"),
    "not_allowed": (CommandStatus.REJECTED, "คำสั่งนี้ไม่ได้รับอนุญาต"),
    "robot_disconnected": (
        CommandStatus.REJECTED,
        "ยังไม่ได้เชื่อมต่อกับหุ่นยนต์ จึงไม่สามารถสั่งงานได้",
    ),
    "robot_fault": (CommandStatus.REJECTED, "หุ่นยนต์ขัดข้อง กรุณาแจ้งเจ้าหน้าที่"),
    "busy": (CommandStatus.REJECTED, "หุ่นยนต์กำลังทำงานอยู่ กรุณารอสักครู่"),
    "person_too_close": (
        CommandStatus.REJECTED,
        "มีคนอยู่ใกล้หุ่นยนต์เกินไป กรุณาถอยออกมาหนึ่งก้าวก่อน",
    ),
    "distance_unknown": (
        CommandStatus.REJECTED,
        "มีคนอยู่ด้านหน้าแต่กล้องวัดระยะไม่ได้ (อาจอยู่ใกล้มาก) หุ่นยนต์จึงยังไม่ขยับ",
    ),
    "sensor_unavailable": (
        CommandStatus.REJECTED,
        "กล้องยังไม่พร้อม หุ่นยนต์จึงยังไม่ขยับเพื่อความปลอดภัย",
    ),
    "cancelled": (CommandStatus.CANCELLED, "หยุดการทำงานของหุ่นยนต์แล้ว"),
    "timeout": (CommandStatus.TIMEOUT, "หุ่นยนต์ไม่ตอบสนองภายในเวลาที่กำหนด จึงสั่งหยุดแล้ว"),
    "connection_lost": (CommandStatus.FAILED, "การเชื่อมต่อกับหุ่นยนต์ขาดหาย"),
    "failed": (CommandStatus.FAILED, "หุ่นยนต์ทำงานตามคำสั่งไม่สำเร็จ"),
}
SOURCE_AUTO = "auto"  # commands the app issues by itself, such as the greeting gesture
_SAFETY_CODES = frozenset({"person_too_close", "distance_unknown", "sensor_unavailable"})


class CommandGateway:
    def __init__(
        self,
        adapter: RobotAdapter,
        allowed_actions: Iterable[str],
        timeout_s: float = 30.0,
        interlock: Callable[[str, str], str | None] | None = None,
    ) -> None:
        self._adapter = adapter
        # (command type, target) -> None, or the outcome code that refuses it.
        self._interlock = interlock
        self._allowed = frozenset(allowed_actions)
        self._timeout_s = timeout_s
        # Held from acceptance until the adapter call really returns, so a
        # command that timed out but is still unwinding keeps the gateway busy.
        self._busy = threading.Lock()
        self._executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="robot-cmd"
        )

    def stop(self, source: str = "touch") -> bool:
        """Request a stop. Never queued, never validated against robot state."""
        logger.warning("STOP requested (source=%s)", source)
        try:
            self._adapter.stop()
        except Exception:
            logger.exception("STOP request failed to reach the robot")
            return False
        return True

    def submit(self, request: CommandRequest) -> CommandResult:
        """Validate and execute a command. Blocks; call from a worker thread."""
        try:
            command_type = CommandType(request.type)
        except (ValueError, TypeError):
            return self._finish(request, "unknown_command")

        target = request.target
        if not isinstance(target, str) or not ID_PATTERN.fullmatch(target):
            return self._finish(request, "invalid_parameter")
        if target not in self._allowed:
            return self._finish(request, "not_allowed")

        try:
            status = self._adapter.get_status()
        except Exception:
            logger.exception("Could not read robot status")
            return self._finish(request, "robot_disconnected")
        if not status.connected or status.state is RobotState.DISCONNECTED:
            return self._finish(request, "robot_disconnected")
        if status.state is RobotState.ERROR:
            return self._finish(request, "robot_fault")
        if status.state is not RobotState.IDLE:
            return self._finish(request, "busy")

        if self._interlock is not None:
            try:
                refusal = self._interlock(command_type.value, target)
            except Exception:
                logger.exception("Safety interlock failed; refusing the command")
                refusal = "sensor_unavailable"
            if refusal is not None:
                return self._finish(request, refusal if refusal in _OUTCOMES else "not_allowed")

        if not self._busy.acquire(blocking=False):
            return self._finish(request, "busy")

        logger.info(
            "Command accepted: id=%s type=%s target=%s source=%s",
            request.command_id, command_type.value, target, request.source,
        )
        try:
            future = self._executor.submit(self._adapter.execute_action, target)
        except RuntimeError:  # gateway already shut down
            self._busy.release()
            return self._finish(request, "failed")
        future.add_done_callback(lambda _future: self._busy.release())

        try:
            outcome: ActionOutcome = future.result(timeout=self._timeout_s)
        except concurrent.futures.TimeoutError:
            self.stop(source="gateway-timeout")
            return self._finish(request, "timeout")
        except concurrent.futures.CancelledError:
            return self._finish(request, "cancelled")
        except RobotConnectionError:
            return self._finish(request, "connection_lost")
        except Exception:
            logger.exception("Adapter raised while executing command %s", request.command_id)
            return self._finish(request, "failed")

        if outcome.cancelled:
            return self._finish(request, "cancelled")
        if not outcome.success:
            return self._finish(request, "failed", outcome.detail)
        return self._finish(request, "ok")

    def shutdown(self) -> None:
        self.stop(source="shutdown")
        self._executor.shutdown(wait=False, cancel_futures=True)

    def _finish(self, request: CommandRequest, code: str, detail: str = "") -> CommandResult:
        status, message = _OUTCOMES[code]
        # The robot's own gestures (one on greeting, if configured) being held back by the
        # safety rule is the rule working, not a problem for the Admin to chase.
        expected = request.source == SOURCE_AUTO and code in _SAFETY_CODES
        level = logging.INFO if status is CommandStatus.SUCCEEDED or expected else logging.WARNING
        logger.log(
            level,
            "Command %s: id=%s type=%r target=%r source=%s code=%s%s",
            status.value, request.command_id, request.type, request.target,
            request.source, code, f" detail={detail}" if detail else "",
        )
        return CommandResult(request.command_id, status, code, message)
