"""Keeps the robot from moving while a person is within its reach.

This is a software precaution built on one forward-facing camera. It cannot see
beside or behind the robot and it is not a substitute for bumpers or a
hardware emergency stop.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable

ACTION = "action"

PERSON_TOO_CLOSE = "person_too_close"
DISTANCE_UNKNOWN = "distance_unknown"
SENSOR_UNAVAILABLE = "sensor_unavailable"


@dataclass(frozen=True)
class Proximity:
    reliable: bool  # the sensor is working and this reading is current
    nearest_m: float | None = None  # None = nobody measured in view
    unmeasured: bool = False  # someone is in view whose distance could not be measured


ProximitySensor = Callable[[], Proximity]


class ProximityInterlock:
    def __init__(
        self,
        sensor: ProximitySensor,
        base_clearance_m: float,
        base_actions: Iterable[str],
    ) -> None:
        self._sensor = sensor
        self._base_clearance_m = base_clearance_m
        self._base_actions = frozenset(base_actions)

    @property
    def base_clearance_m(self) -> float:
        return self._base_clearance_m

    def clearance_for(self, command_type: str, target: str) -> float | None:
        """How far away people must be for this command; None = it moves nothing."""
        if target in self._base_actions:
            return self._base_clearance_m
        return None

    def check(self, command_type: str, target: str) -> str | None:
        """None if the command may run (or keep running), otherwise the reason it may not."""
        clearance = self.clearance_for(command_type, target)
        if clearance is None:
            return None
        try:
            proximity = self._sensor()
        except Exception:
            return SENSOR_UNAVAILABLE
        if not proximity.reliable:
            return SENSOR_UNAVAILABLE  # not knowing is treated as not safe
        if proximity.nearest_m is not None and proximity.nearest_m < clearance:
            return PERSON_TOO_CLOSE
        if proximity.unmeasured:
            # Often someone closer than stereo depth can measure: not knowing is not safe.
            return DISTANCE_UNKNOWN
        return None
