"""Does someone in front of the camera look like they want to talk to it?

A person walking through, or past, is not greeted: only one who stops and stays in front of the
camera. "Stands still, facing it" is judged from where the nearest person in front is over the last
moments: if they stay within a small circle for ``still_s`` seconds, they have stopped. (The camera
reports people's positions, not their faces, so standing still in front of it is the signal.)
"""
from __future__ import annotations

import statistics
from collections import deque
from typing import Iterable

from vision.interfaces import PersonObservation

STILL_RADIUS_M = 0.30  # moving less than this, in any direction, counts as standing still
MIN_SAMPLE_SHARE = 0.6  # of the camera's readings in the window, the share that must have seen them
WINDOW_SLACK = 0.9  # the window must be nearly as old as ``still_s`` before it counts


class AttentionTracker:
    def __init__(
        self,
        still_s: float,
        max_distance_m: float,
        lateral_limit_m: float,
        min_confidence: float = 0.5,
        radius_m: float = STILL_RADIUS_M,
    ) -> None:
        self._still_s = still_s
        self._max_distance = max_distance_m
        self._lateral = lateral_limit_m
        self._min_confidence = min_confidence
        self._radius = radius_m
        self._samples: deque[tuple[float, float, float]] = deque()  # (time, x, z) of the nearest person
        self._ticks: deque[float] = deque()  # every update, seen or not

    def set_limits(self, max_distance_m: float, lateral_limit_m: float) -> None:
        self._max_distance, self._lateral = max_distance_m, lateral_limit_m

    def reset(self) -> None:
        self._samples.clear()
        self._ticks.clear()

    def update(self, people: Iterable[PersonObservation], now: float) -> bool:
        """True while the nearest person in front has stood still for ``still_s`` seconds."""
        if self._still_s <= 0.0:
            return True  # switched off: anyone who has arrived counts
        candidates = [
            p for p in people
            if p.confidence >= self._min_confidence and p.measured
            and p.z_m <= self._max_distance and abs(p.x_m) <= self._lateral
        ]
        self._ticks.append(now)
        if candidates:
            nearest = min(candidates, key=lambda p: p.z_m)
            self._samples.append((now, nearest.x_m, nearest.z_m))
        horizon = now - self._still_s
        while self._samples and self._samples[0][0] < horizon:
            self._samples.popleft()
        while self._ticks and self._ticks[0] < horizon:
            self._ticks.popleft()
        if len(self._samples) < 3 or len(self._ticks) == 0:
            return False
        if now - self._samples[0][0] < self._still_s * WINDOW_SLACK:
            return False  # not there for long enough yet
        if len(self._samples) < MIN_SAMPLE_SHARE * len(self._ticks):
            return False  # in and out of view
        xs = [x for _t, x, _z in self._samples]
        zs = [z for _t, _x, z in self._samples]
        centre_x, centre_z = statistics.median(xs), statistics.median(zs)
        return all(
            abs(x - centre_x) <= self._radius and abs(z - centre_z) <= self._radius for x, z in zip(xs, zs)
        )
