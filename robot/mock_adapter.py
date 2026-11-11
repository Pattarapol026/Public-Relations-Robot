"""In-memory robot simulator for development without hardware."""
from __future__ import annotations

import logging
import threading

from robot.interfaces import (
    ActionOutcome,
    RobotAdapter,
    RobotConnectionError,
    RobotState,
    RobotStatus,
)

logger = logging.getLogger(__name__)


class MockRobotAdapter(RobotAdapter):
    def __init__(self, action_time_s: float = 1.5) -> None:
        self._action_time_s = action_time_s
        self._lock = threading.Lock()
        self._cancel = threading.Event()
        self._connected = False
        self._fault = False
        self._state = RobotState.DISCONNECTED

    def connect(self) -> None:
        with self._lock:
            self._connected = True
            self._state = RobotState.ERROR if self._fault else RobotState.IDLE
        logger.info("Mock robot connected")

    def disconnect(self) -> None:
        with self._lock:
            self._connected = False
            self._state = RobotState.DISCONNECTED
        self._cancel.set()
        logger.info("Mock robot disconnected")

    def get_status(self) -> RobotStatus:
        with self._lock:
            return RobotStatus(
                connected=self._connected,
                state=self._state,
                battery_percent=87.0 if self._connected else None,
                detail="simulated",
            )

    def stop(self) -> None:
        self._cancel.set()
        logger.info("Mock robot stop requested")

    def execute_action(self, action_id: str) -> ActionOutcome:
        return self._run(self._action_time_s)

    # --- simulation controls (Admin test panel and unit tests) ---

    def simulate_connection_loss(self, lost: bool) -> None:
        if lost:
            self.disconnect()
        else:
            self.connect()

    def simulate_fault(self, fault: bool) -> None:
        with self._lock:
            self._fault = fault
            if self._connected:
                self._state = RobotState.ERROR if fault else RobotState.IDLE
        if fault:
            self._cancel.set()

    def _run(self, duration_s: float) -> ActionOutcome:
        with self._lock:
            if not self._connected:
                raise RobotConnectionError("mock robot is not connected")
            if self._state is not RobotState.IDLE:
                return ActionOutcome(success=False, detail=f"robot is {self._state.value}")
            self._cancel.clear()
            self._state = RobotState.EXECUTING

        interrupted = self._cancel.wait(duration_s)

        with self._lock:
            if not self._connected:
                raise RobotConnectionError("mock robot connection lost")
            if self._fault:
                return ActionOutcome(success=False, detail="robot fault")
            self._state = RobotState.IDLE
        if interrupted:
            return ActionOutcome(success=False, cancelled=True, detail="stopped")
        return ActionOutcome(success=True)
