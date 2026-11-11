"""A vision source driven by code: for development without a camera and for tests."""
from __future__ import annotations

import time

from vision.interfaces import PersonObservation, SourceStatus, VisionSnapshot, VisionSource
from vision.signs import Sign


class MockVisionSource(VisionSource):
    def __init__(self) -> None:
        self._people: tuple[PersonObservation, ...] = ()
        self._status = SourceStatus.STARTING
        self._frozen = False
        self._frozen_at = 0.0
        self._frame: bytes | None = None
        self._signs: tuple[Sign, ...] = ()

    def start(self) -> None:
        self._status = SourceStatus.RUNNING

    def stop(self) -> None:
        self._status = SourceStatus.DISABLED

    def latest(self) -> VisionSnapshot | None:
        if self._status is not SourceStatus.RUNNING:
            return None
        timestamp = self._frozen_at if self._frozen else time.monotonic()
        return VisionSnapshot(timestamp, self._people)

    @property
    def status(self) -> SourceStatus:
        return self._status

    @property
    def detail(self) -> str:
        return "simulated camera"

    def latest_frame(self) -> bytes | None:
        return self._frame if self._status is SourceStatus.RUNNING else None

    def take_sign_cycles(self) -> list[list[Sign]]:
        # One "picture examined" per call, showing whatever is being held up now.
        return [list(self._signs)] if self._status is SourceStatus.RUNNING else []

    # --- simulation controls ---

    def show_signs(self, *signs: Sign) -> None:
        """Hold these cards/gestures up to the camera until changed."""
        self._signs = tuple(signs)

    def set_frame(self, jpeg: bytes | None) -> None:
        self._frame = jpeg

    def set_people(self, *people: PersonObservation) -> None:
        self._people = tuple(people)

    def place_person(
        self,
        distance_m: float,
        x_m: float = 0.0,
        box: tuple[float, float, float, float] | None = (0.35, 0.2, 0.65, 0.95),
    ) -> None:
        self.set_people(PersonObservation(x_m=x_m, z_m=distance_m, confidence=0.9, box=box))

    def clear(self) -> None:
        self._people = ()

    def freeze(self) -> None:
        """Stop producing new snapshots, as a stalled camera would."""
        self._frozen = True
        self._frozen_at = time.monotonic()
