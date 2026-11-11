"""Turns a stream of person detections into "a visitor arrived" / "the visitor left"."""
from __future__ import annotations

import statistics
from collections import deque
from dataclasses import dataclass
from enum import Enum
from typing import Iterable

from vision.interfaces import PersonObservation


class PresenceEventType(Enum):
    ARRIVED = "arrived"
    LEFT = "left"


@dataclass(frozen=True)
class PresenceEvent:
    type: PresenceEventType
    distance_m: float | None = None  # ARRIVED: how close the visitor is
    duration_s: float = 0.0  # LEFT: how long the visit lasted
    closest_m: float | None = None  # LEFT: closest approach during the visit


@dataclass(frozen=True)
class PresenceConfig:
    engage_distance_m: float = 1.8  # closer than this, for dwell_s -> a visitor has arrived
    release_distance_m: float = 2.5  # farther than this (or gone), for leave_s -> they left
    lateral_limit_m: float = 1.0  # ignore people this far to either side (walking past)
    dwell_s: float = 0.8
    leave_s: float = 4.0
    min_confidence: float = 0.5
    min_distance_m: float = 0.3  # stereo depth is unreliable closer than this
    smoothing_samples: int = 5  # median over this many readings rejects one-frame glitches


class PresenceTracker:
    """Feed it every snapshot's people (or nothing, when the camera has no data)."""

    def __init__(self, config: PresenceConfig) -> None:
        self._config = config
        self._window: deque[float | None] = deque(maxlen=config.smoothing_samples)
        self._present = False
        self._candidate_since: float | None = None
        self._last_in_range = 0.0
        self._visit_started = 0.0
        self._closest: float | None = None
        self._distance: float | None = None
        self._people_count = 0
        self._unmeasured_count = 0

    @property
    def config(self) -> PresenceConfig:
        return self._config

    def set_config(self, config: PresenceConfig) -> None:
        """New distances and times; a visitor already present stays present."""
        if config.smoothing_samples != self._window.maxlen:
            self._window = deque(self._window, maxlen=config.smoothing_samples)
        self._config = config

    @property
    def present(self) -> bool:
        return self._present

    @property
    def distance_m(self) -> float | None:
        """Smoothed distance of the nearest person in front, or None if nobody is."""
        return self._distance

    @property
    def people_count(self) -> int:
        return self._people_count

    @property
    def unmeasured_count(self) -> int:
        """People seen with confidence but without a usable distance."""
        return self._unmeasured_count

    def update(self, people: Iterable[PersonObservation], now: float) -> list[PresenceEvent]:
        config = self._config
        people = tuple(people)
        in_front = [
            person.z_m
            for person in people
            if person.confidence >= config.min_confidence
            and person.z_m >= config.min_distance_m
            and abs(person.x_m) <= config.lateral_limit_m
        ]
        self._people_count = len(in_front)
        self._unmeasured_count = sum(
            1 for person in people if person.confidence >= config.min_confidence and not person.measured
        )
        self._window.append(min(in_front) if in_front else None)
        self._distance = self._smoothed()

        if not self._present:
            if self._distance is not None and self._distance <= config.engage_distance_m:
                if self._candidate_since is None:
                    self._candidate_since = now
                if now - self._candidate_since >= config.dwell_s:
                    self._present = True
                    self._candidate_since = None
                    self._visit_started = now
                    self._last_in_range = now
                    self._closest = self._distance
                    return [PresenceEvent(PresenceEventType.ARRIVED, distance_m=self._distance)]
            else:
                self._candidate_since = None
            return []

        if self._distance is not None and self._distance <= config.release_distance_m:
            self._last_in_range = now
            self._closest = min(self._closest or self._distance, self._distance)
        elif self._distance is None and self._unmeasured_count:
            # Still seen, only without depth (often because they stepped very
            # close): the visit goes on. This never starts a visit, since the
            # distance is not known.
            self._last_in_range = now
        elif now - self._last_in_range >= config.leave_s:
            self._present = False
            return [
                PresenceEvent(
                    PresenceEventType.LEFT,
                    # The visit ended when they were last seen, not when we gave up waiting.
                    duration_s=self._last_in_range - self._visit_started,
                    closest_m=self._closest,
                )
            ]
        return []

    def _smoothed(self) -> float | None:
        readings = [value for value in self._window if value is not None]
        # A person must be there in most recent readings; one stray detection is not a person.
        if len(readings) * 2 <= len(self._window):
            return None
        return statistics.median(readings)
