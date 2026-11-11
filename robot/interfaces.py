"""Transport-neutral robot interface.

Nothing outside ``robot/`` may know about ROS topics, MQTT topics or message
formats. The rest of the app talks to the robot only through ``CommandGateway``,
which in turn talks to a ``RobotAdapter``.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum


class RobotError(Exception):
    """Base class for adapter failures."""


class RobotConnectionError(RobotError):
    """The robot (or its bridge) cannot be reached."""


class RobotState(str, Enum):
    DISCONNECTED = "disconnected"
    IDLE = "idle"
    EXECUTING = "executing"
    ERROR = "error"


@dataclass(frozen=True)
class RobotStatus:
    connected: bool
    state: RobotState
    battery_percent: float | None = None
    detail: str = ""


@dataclass(frozen=True)
class ActionOutcome:
    success: bool
    cancelled: bool = False
    detail: str = ""


class RobotAdapter(ABC):
    """High-level robot commands. Implementations must be thread-safe."""

    @abstractmethod
    def connect(self) -> None:
        """Open the connection. Raises ``RobotConnectionError`` on failure."""

    @abstractmethod
    def disconnect(self) -> None:
        """Close the connection. Must not raise."""

    @abstractmethod
    def get_status(self) -> RobotStatus:
        """Return the latest known status.

        Must return immediately from a cached snapshot (it is polled from the
        GUI thread) and must not raise when the robot is unreachable — report
        ``connected=False`` instead.
        """

    @abstractmethod
    def stop(self) -> None:
        """Cancel any motion/action in progress.

        Must return quickly, be callable from any thread while another command
        is blocking, and need nothing but the robot link itself. This is a
        software stop request, not a substitute for a hardware emergency stop.
        """

    @abstractmethod
    def execute_action(self, action_id: str) -> ActionOutcome:
        """Run a predefined action. Blocks until done, failed or stopped."""
