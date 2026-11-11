"""Qt-side bridge to the robot: keeps gateway calls off the GUI thread."""
from __future__ import annotations

import dataclasses
import logging
from typing import Callable

from PySide6.QtCore import QObject, QThreadPool, QTimer, Signal, Slot

from app.workers import FunctionWorker
from robot.command_gateway import CommandGateway, CommandRequest, CommandResult, CommandStatus
from robot.interfaces import RobotAdapter, RobotState, RobotStatus
from robot.safety import ProximityInterlock

STATUS_POLL_MS = 500
GUARD_POLL_MS = 100
SAFETY_STOP_MESSAGE = "หยุดการเคลื่อนไหวแล้ว เพราะมีคนเข้ามาใกล้หุ่นยนต์"
# With the simulator there is no hardware: "done" must not read as "the robot moved".
SIMULATED_SUCCESS_MESSAGE = (
    "หุ่นยนต์จำลองทำคำสั่งเรียบร้อย (ยังไม่ได้เชื่อมต่อหุ่นยนต์จริง จึงไม่มีการเคลื่อนไหวจริง)"
)

logger = logging.getLogger(__name__)

# (actor, action, detail) -> None; records robot commands in the activity log.
AuditSink = Callable[[str, str, str], None]


class RobotController(QObject):
    command_submitted = Signal(object)  # CommandRequest: handed to the gateway, result to follow
    command_finished = Signal(object)  # CommandResult
    status_changed = Signal(object)  # RobotStatus
    safety_stopped = Signal(str)  # a movement was stopped part-way; the reason code

    def __init__(
        self,
        gateway: CommandGateway,
        adapter: RobotAdapter,
        pool: QThreadPool,
        audit: AuditSink | None = None,
        interlock: ProximityInterlock | None = None,
        simulated: bool = False,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._gateway = gateway
        self._adapter = adapter
        self._pool = pool
        self._audit = audit or (lambda actor, action, detail: None)
        self._interlock = interlock
        self._simulated = simulated
        self._pending: dict[str, CommandRequest] = {}
        self._quiet: set[str] = set()
        self._guard_fired: set[str] = set()
        self._last: tuple[CommandRequest, CommandResult] | None = None
        self._status = self._read_status()

        self._poll_timer = QTimer(self)
        self._poll_timer.timeout.connect(self._poll)
        self._poll_timer.start(STATUS_POLL_MS)

        # The gateway checks for people before a movement starts; this keeps
        # checking while it runs, since someone may step in part-way.
        self._guard_timer = QTimer(self)
        self._guard_timer.timeout.connect(self._guard)
        if interlock is not None:
            self._guard_timer.start(GUARD_POLL_MS)

    @property
    def status(self) -> RobotStatus:
        return self._status

    @property
    def simulated(self) -> bool:
        """True with the built-in simulator: commands succeed but nothing physical moves."""
        return self._simulated

    @property
    def pending(self) -> list[CommandRequest]:
        """Commands handed to the gateway whose result has not come back yet."""
        return list(self._pending.values())

    @property
    def last_command(self) -> tuple[CommandRequest, CommandResult] | None:
        """The most recent finished command and its result."""
        return self._last

    def send(self, command_type: str, target: str, source: str, quiet: bool = False) -> bool:
        """Submit a command. ``quiet`` ones (the robot's own gestures) report no result on screen.

        False if the same command is still waiting for its result: a double tap
        must not queue the movement twice.
        """
        for pending in self._pending.values():
            if (pending.type, pending.target) == (command_type, target):
                logger.info(
                    "Duplicate command ignored: type=%s target=%s source=%s (id=%s still running)",
                    command_type, target, source, pending.command_id,
                )
                return False
        request = CommandRequest(type=command_type, target=target, source=source)
        self._pending[request.command_id] = request
        if quiet:
            self._quiet.add(request.command_id)
        worker = FunctionWorker(request.command_id, self._gateway.submit, request)
        worker.signals.finished.connect(self._on_finished)
        worker.signals.failed.connect(self._on_failed)
        self._pool.start(worker)
        self.command_submitted.emit(request)
        return True

    def stop(self, source: str) -> bool:
        # Called straight from the GUI thread: the stop path must not wait in
        # the thread pool behind a running command, nor on the audit write.
        delivered = self._gateway.stop(source=source)
        self._audit(source, "robot.stop", "delivered" if delivered else "NOT delivered")
        return delivered

    def shutdown(self) -> None:
        self._poll_timer.stop()
        self._guard_timer.stop()
        self._gateway.shutdown()

    def _guard(self) -> None:
        if not self._pending:
            return
        if self._read_status().state is not RobotState.EXECUTING:
            return
        for command_id, request in self._pending.items():
            if command_id in self._guard_fired:
                continue  # already stopped; it is still winding down
            reason = self._interlock.check(str(request.type), str(request.target))
            if reason is not None:
                self._guard_fired.update(self._pending)
                self._gateway.stop(source="safety")
                self._audit("safety", "robot.stop", f"reason={reason} target={request.target}")
                self.safety_stopped.emit(reason)
                return

    @Slot(object, object)
    def _on_finished(self, command_id: str, result: object) -> None:
        if command_id in self._guard_fired and result.status is CommandStatus.CANCELLED:
            # Say why it stopped, rather than the general "stopped" message.
            result = dataclasses.replace(result, message=SAFETY_STOP_MESSAGE)
        elif self._simulated and result.ok:
            result = dataclasses.replace(result, message=SIMULATED_SUCCESS_MESSAGE)
        self._record(command_id, result)
        self._poll()
        self._report(command_id, result)

    @Slot(object, str)
    def _on_failed(self, command_id: str, _error: str) -> None:
        result = CommandResult(
            command_id, CommandStatus.FAILED, "failed", "หุ่นยนต์ทำงานตามคำสั่งไม่สำเร็จ"
        )
        self._record(command_id, result)
        self._report(command_id, result)

    def _report(self, command_id: str, result: CommandResult) -> None:
        if command_id in self._quiet:
            self._quiet.discard(command_id)
            return
        self.command_finished.emit(result)

    def _record(self, command_id: str, result: CommandResult) -> None:
        request = self._pending.pop(command_id, None)
        self._guard_fired.discard(command_id)
        if request is not None:
            self._last = (request, result)
            self._audit(
                request.source,
                f"robot.{request.type}",
                f"target={request.target} result={result.status.value}/{result.code}",
            )

    def _poll(self) -> None:
        status = self._read_status()
        if status != self._status:
            self._status = status
            self.status_changed.emit(status)

    def _read_status(self) -> RobotStatus:
        try:
            return self._adapter.get_status()
        except Exception:
            return RobotStatus(connected=False, state=RobotState.DISCONNECTED)
