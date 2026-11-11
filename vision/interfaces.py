"""Camera-neutral vision interface.

The rest of the app sees ``VisionSnapshot`` objects — who is in front of the
robot and how far away — and, optionally, a live JPEG picture for display.
Nothing outside ``vision/`` depends on a particular camera, so a source running
on the robot's ROS computer can replace the USB one later.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum

from vision.signs import Sign


class SourceStatus(Enum):
    DISABLED = "disabled"
    STARTING = "starting"
    RUNNING = "running"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class PersonObservation:
    """One detected person, in metres, relative to the camera.

    ``x_m`` is sideways (positive = the camera's right), ``z_m`` is straight ahead.
    ``box`` is where the person appears in the camera picture, as
    (xmin, ymin, xmax, ymax) fractions of its width and height, if known.
    ``y_m`` is the height of the detection's centre relative to the camera axis,
    as DepthAI reports it (``spatialCoordinates.y``), or None if the source
    gives no height. Its sign convention must be checked on the mounted camera.
    """

    x_m: float
    z_m: float
    confidence: float
    box: tuple[float, float, float, float] | None = None
    y_m: float | None = None

    @property
    def measured(self) -> bool:
        """False when the camera saw the person but had no valid depth for them.

        The OAK-D then reports all coordinates as exactly zero (for example when
        the person is closer than the depth range, or on a surface stereo cannot
        match). Such a person is there, at an unknown distance — not at 0 m.
        """
        return self.z_m > 0.0


@dataclass(frozen=True)
class VisionSnapshot:
    timestamp: float  # time.monotonic() when the camera produced it
    people: tuple[PersonObservation, ...]


class VisionSource(ABC):
    @abstractmethod
    def start(self) -> None:
        """Begin capturing in the background. Must return immediately and not raise."""

    @abstractmethod
    def stop(self) -> None:
        """Stop capturing and release the camera. Must not raise."""

    @abstractmethod
    def latest(self) -> VisionSnapshot | None:
        """Most recent snapshot, or None if there is none yet. Must not block."""

    def latest_frame(self) -> bytes | None:
        """Most recent camera picture as JPEG bytes, for on-screen display only.

        None if this source has no live picture (preview off, or not supported).
        """
        return None

    def take_sign_cycles(self) -> list[list[Sign]]:
        """Cards and gestures seen since the last call: one list per picture examined.

        An empty inner list means a picture was examined and showed nothing,
        which is how the app learns that a sign is no longer being held up.
        """
        return []

    def take_mouth_readings(self) -> list[tuple[float, float | None]]:
        """How open the nearest person's jaw was in the pictures examined since the last call: ``(time.monotonic(), value)``,
        value None when no face was found in that picture. A source that cannot tell returns nothing."""
        return []

    @property
    @abstractmethod
    def status(self) -> SourceStatus: ...

    @property
    @abstractmethod
    def detail(self) -> str:
        """Human-readable explanation of the status (device name, or why it is unavailable)."""
