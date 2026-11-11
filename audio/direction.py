"""Direction of a voice (direction of arrival, DOA), from a microphone that measures it.

Only some microphones can tell where a voice comes from: an array with known
geometry and firmware or software that computes the angle (for example the
ReSpeaker XVF3800). A laptop's built-in microphone is not treated as one: its
geometry is unknown and two microphones cannot tell front from back.

Angles are in degrees in the microphone's own frame: 0 = straight ahead of the
microphone, positive = to the microphone's right, in (-180, 180]. A source
converts its device's convention to this one.

Nothing here invents a direction: without a ``DirectionSource`` that has real
readings there is no estimate, and the app says so.
"""
from __future__ import annotations

import math
import time
from abc import ABC, abstractmethod
from collections import deque
from dataclasses import dataclass


@dataclass(frozen=True)
class DirectionReading:
    timestamp: float  # time.monotonic()
    azimuth_deg: float
    confidence: float  # 0–1 as the device reports it (1.0 if it gives none)


@dataclass(frozen=True)
class DirectionEstimate:
    azimuth_deg: float
    confidence: float  # 0–1: agreement of the readings × their own confidence
    spread_deg: float  # circular standard deviation of the readings used
    readings: int
    stable: bool  # spread small enough to be relied on
    timestamp: float  # time of the newest reading used


class DirectionSource(ABC):
    """A device that reports voice directions. ``read`` must not block."""

    @property
    @abstractmethod
    def available(self) -> bool:
        """True only while real readings can come from the device."""

    @property
    @abstractmethod
    def status_text(self) -> str:
        """For the Admin: what the source is and, if unavailable, why."""

    @abstractmethod
    def read(self) -> list[DirectionReading]:
        """Readings since the last call (empty if none)."""

    def start(self) -> None:
        """Begin reading. Must not raise."""

    def stop(self) -> None:
        """Stop reading and release the device. Must not raise."""


class UnavailableDirectionSource(DirectionSource):
    """No microphone that measures direction: the honest default."""

    def __init__(self, reason: str) -> None:
        self._reason = reason

    @property
    def available(self) -> bool:
        return False

    @property
    def status_text(self) -> str:
        return self._reason

    def read(self) -> list[DirectionReading]:
        return []


def wrap_degrees(angle: float) -> float:
    """``angle`` in (-180, 180]."""
    angle = math.fmod(angle, 360.0)
    if angle <= -180.0:
        angle += 360.0
    elif angle > 180.0:
        angle -= 360.0
    return angle


def angle_between(a: float, b: float) -> float:
    """Smallest absolute difference of two angles, 0–180."""
    return abs(wrap_degrees(a - b))


@dataclass(frozen=True)
class TrackerConfig:
    window_s: float = 1.5  # readings older than this are forgotten
    min_confidence: float = 0.3  # readings the device itself doubts are ignored
    min_readings: int = 3
    outlier_deg: float = 45.0  # readings this far from the rest are dropped (a door, an echo)
    max_spread_deg: float = 20.0  # above this the estimate is shown but not relied on


class DirectionTracker:
    """Keeps the direction of the current speaker from a stream of readings.

    Readings count only while someone is speaking (``speech_active``) and the
    robot itself is silent: the robot's own voice from its speaker, or room
    noise between words, must not look like a new speaker.
    """

    def __init__(self, config: TrackerConfig | None = None) -> None:
        self._config = config or TrackerConfig()
        self._readings: deque[DirectionReading] = deque()
        self.ignored_while_robot_spoke = 0
        self.ignored_without_speech = 0

    def reset(self) -> None:
        self._readings.clear()

    def add(self, reading: DirectionReading, speech_active: bool, robot_speaking: bool) -> None:
        if robot_speaking:
            self.ignored_while_robot_spoke += 1
            return
        if not speech_active:
            self.ignored_without_speech += 1
            return
        if reading.confidence >= self._config.min_confidence:
            self._readings.append(reading)

    def estimate(self, now: float | None = None) -> DirectionEstimate | None:
        """The speaker's direction from recent readings, or None if there are too few."""
        config = self._config
        now = time.monotonic() if now is None else now
        while self._readings and now - self._readings[0].timestamp > config.window_s:
            self._readings.popleft()
        readings = list(self._readings)
        if len(readings) < config.min_readings:
            return None
        centre = _circular_mean(readings)[0]
        kept = [r for r in readings if angle_between(r.azimuth_deg, centre) <= config.outlier_deg]
        if len(kept) < config.min_readings:
            return None
        mean, resultant = _circular_mean(kept)
        spread = math.degrees(math.sqrt(max(0.0, -2.0 * math.log(max(resultant, 1e-9)))))
        own = sum(r.confidence for r in kept) / len(kept)
        agreement = len(kept) / len(readings)
        return DirectionEstimate(
            azimuth_deg=round(mean, 1),
            confidence=round(resultant * own * agreement, 3),
            spread_deg=round(spread, 1),
            readings=len(kept),
            stable=spread <= config.max_spread_deg,
            timestamp=kept[-1].timestamp,
        )


def _circular_mean(readings: list[DirectionReading]) -> tuple[float, float]:
    """Confidence-weighted mean angle and the mean resultant length (0–1)."""
    weight = sum(r.confidence for r in readings) or 1.0
    s = sum(r.confidence * math.sin(math.radians(r.azimuth_deg)) for r in readings) / weight
    c = sum(r.confidence * math.cos(math.radians(r.azimuth_deg)) for r in readings) / weight
    return wrap_degrees(math.degrees(math.atan2(s, c))), math.hypot(s, c)
