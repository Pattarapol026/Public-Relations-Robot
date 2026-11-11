"""Turns what a visitor says, types or taps into robot commands, politely and safely.

Every command still goes through ``CommandGateway``; this class only decides
what to ask for and what to tell the visitor: when a person is standing too close
it asks them to step back and waits, instead of refusing.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Sequence

from PySide6.QtCore import QObject, QTimer, Signal

from app.robot_controller import RobotController
from config.settings import NamedTarget
from robot.interfaces import RobotState
from robot.safety import ACTION, DISTANCE_UNKNOWN, PERSON_TOO_CLOSE, ProximityInterlock
from services.command_intent import INTENT_ACTION, INTENT_STOP, parse
from services.conversation_service import LANG_ENGLISH, detect_language

logger = logging.getLogger(__name__)

CLEARANCE_TIMEOUT_MS = 10_000  # how long to wait for the visitor to step back
CLEARANCE_POLL_MS = 200
CLEAR_POLLS_NEEDED = 3  # the space must stay clear this many polls in a row

SENSOR_MESSAGE = "กล้องยังไม่พร้อม ผมจึงยังไม่ขยับเพื่อความปลอดภัยครับ"
SENSOR_MESSAGE_EN = "The camera is not ready, so I will not move, for safety."
# Someone within reach, or seen too close for the camera to measure: either way,
# ask them to step back and wait rather than refuse.
_STEP_BACK = frozenset({PERSON_TOO_CLOSE, DISTANCE_UNKNOWN})


@dataclass(frozen=True)
class _Waiting:
    target: NamedTarget
    source: str


class CommandController(QObject):
    notice = Signal(str, str)  # text, level: something happened after the reply was given

    def __init__(
        self,
        actions: Sequence[NamedTarget],
        robot: RobotController,
        interlock: ProximityInterlock | None = None,
        understands_speech: bool = True,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._actions = {target.id: target for target in actions}
        self._robot = robot
        self._interlock = interlock
        self._understands_speech = understands_speech
        self._waiting: _Waiting | None = None
        self._clear_polls = 0
        self._english = False  # the visitor's last spoken or typed command was in English

        self._clearance_timer = QTimer(self)
        self._clearance_timer.setInterval(CLEARANCE_POLL_MS)
        self._clearance_timer.timeout.connect(self._check_clearance)
        self._clearance_deadline = QTimer(self)
        self._clearance_deadline.setSingleShot(True)
        self._clearance_deadline.timeout.connect(self._give_up_waiting)

    @property
    def waiting_for_space(self) -> bool:
        return self._waiting is not None

    def handle_text(self, text: str, mode: str) -> str | None:
        """The robot's reply if ``text`` was a command, else None."""
        if not self._understands_speech:
            return None
        self._english = detect_language(text) == LANG_ENGLISH
        source = "voice" if mode == "voice" else "chat"

        intent = parse(text, tuple(self._actions.values()))
        if intent is None:
            return None
        if intent.kind == INTENT_STOP:
            self.cancel()
            if self._robot.stop(source=source):
                return self._say("หยุดแล้วครับ", "Stopped.")
            return self._say(
                "ส่งคำสั่งหยุดไม่สำเร็จ กรุณาใช้ปุ่มหยุดฉุกเฉินที่ตัวหุ่นยนต์",
                "The stop command failed. Please use the emergency stop button on the robot.",
            )
        if intent.kind == INTENT_ACTION:
            return self._execute(intent.target, source)
        return None

    def request(self, target_id: str, source: str) -> str:
        """A button was pressed. Returns what to tell the visitor."""
        self._english = False  # the buttons are labelled in Thai
        target = self._actions.get(target_id)
        if target is None:
            # Not ours to judge: let the gateway refuse it and log the attempt.
            self._robot.send(ACTION, target_id, source)
            return "กำลังส่งคำสั่งไปยังหุ่นยนต์..."
        self.cancel()
        return self._execute(target, source)

    def cancel(self) -> None:
        """Forget anything still waiting: the visitor has left or said stop."""
        self._stop_waiting()

    # --- internals ---

    def _say(self, thai: str, english: str) -> str:
        return english if self._english else thai

    def _execute(self, target: NamedTarget, source: str) -> str:
        status = self._robot.status
        if not status.connected or status.state is RobotState.DISCONNECTED:
            return self._say("ยังไม่ได้เชื่อมต่อกับหุ่นยนต์ จึงสั่งงานไม่ได้ครับ", "The robot is not connected, so I cannot do that.")
        if status.state is RobotState.ERROR:
            return self._say("หุ่นยนต์ขัดข้องอยู่ครับ กรุณาแจ้งเจ้าหน้าที่", "The robot has a fault. Please tell a member of staff.")
        if status.state is not RobotState.IDLE:
            return self._say("หุ่นยนต์กำลังทำงานอยู่ครับ กรุณารอสักครู่", "The robot is busy. Please wait a moment.")

        reason = self._interlock.check(ACTION, target.id) if self._interlock else None
        if reason in _STEP_BACK:
            self._waiting = _Waiting(target, source)
            self._clear_polls = 0
            self._clearance_timer.start()
            self._clearance_deadline.start(CLEARANCE_TIMEOUT_MS)
            return self._say(
                f"ขอพื้นที่หน่อยนะครับ ถอยออกไปหนึ่งก้าว แล้วผมจะ{target.label}ให้ดู",
                f"Please take one step back to give me some room: {target.label}.",
            )
        if reason is not None:
            return self._say(SENSOR_MESSAGE, SENSOR_MESSAGE_EN)

        self._robot.send(ACTION, target.id, source)
        return self._say(f"ได้ครับ {target.label}นะครับ", f"Certainly: {target.label}.")

    def _check_clearance(self) -> None:
        waiting = self._waiting
        if waiting is None:
            self._clearance_timer.stop()
            return
        reason = self._interlock.check(ACTION, waiting.target.id)
        if reason in _STEP_BACK:
            self._clear_polls = 0
            return
        if reason is not None:
            self._stop_waiting()
            self.notice.emit(SENSOR_MESSAGE, "error")
            return
        self._clear_polls += 1
        if self._clear_polls < CLEAR_POLLS_NEEDED:
            return
        self._stop_waiting()
        if self._robot.status.state is RobotState.IDLE:
            self._robot.send(ACTION, waiting.target.id, waiting.source)
            self.notice.emit("ขอบคุณครับ", "info")

    def _give_up_waiting(self) -> None:
        if self._waiting is not None:
            self._stop_waiting()
            self.notice.emit("ยกเลิกคำสั่งแล้วครับ เพราะยังมีคนอยู่ใกล้หุ่นยนต์", "error")

    def _stop_waiting(self) -> None:
        self._waiting = None
        self._clearance_timer.stop()
        self._clearance_deadline.stop()
