"""Which of the people the camera sees is the one speaking, from the voice's direction.

Coordinates: the camera's frame, in metres, as ``PersonObservation`` gives it
(x = to the camera's right, z = straight ahead, y = height as DepthAI reports it).
The microphone sits at ``(mic_x_m, mic_z_m)`` in that frame and is turned
``mic_yaw_deg`` to the right of the camera's forward axis; a person's direction
as the microphone sees it is ``atan2(x - mic_x, z - mic_z) - mic_yaw``.

The answer is never a guess: with no direction, an unsteady one, people at
similar angles, or a voice from outside the camera's view, it says which of
these it is instead of picking someone.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Sequence

from audio.direction import DirectionEstimate, angle_between, wrap_degrees
from vision.interfaces import PersonObservation


class MatchStatus(Enum):
    NO_DIRECTION = "no_direction"  # no direction measurement (no capable microphone, or nobody spoke)
    NO_CAMERA = "no_camera"  # the camera gives no fresh detections: nobody can be matched
    UNSTEADY = "unsteady"  # readings disagree too much
    OUT_OF_VIEW = "out_of_view"  # the voice comes from outside the camera's view
    NO_PERSON = "no_person"  # in view, but nobody is in that direction
    AMBIGUOUS = "ambiguous"  # several people in about that direction
    UNMEASURED = "unmeasured"  # someone is there, but the camera has no depth for them
    MATCHED = "matched"


@dataclass(frozen=True)
class MountGeometry:
    mic_x_m: float = 0.0
    mic_z_m: float = 0.0
    mic_yaw_deg: float = 0.0
    camera_hfov_deg: float = 69.0  # OAK-D colour camera, horizontal field of view


@dataclass(frozen=True)
class MatchConfig:
    tolerance_deg: float = 15.0  # a person within this of the voice's direction may be the speaker
    margin_deg: float = 10.0  # the best must be this much closer than the next to be chosen


@dataclass(frozen=True)
class SpeakerMatch:
    status: MatchStatus
    reason: str
    person: PersonObservation | None = None
    person_azimuth_deg: float | None = None
    error_deg: float | None = None
    at_conversation_distance: bool = False
    people_seen: int = 0

    @property
    def from_visitor(self) -> bool | None:
        """True: the voice is the visitor's at conversation distance. False: confidently not
        (it comes from a direction in view where nobody is, or from someone far off).
        None: cannot tell — nothing should be decided from it."""
        if self.status is MatchStatus.MATCHED:
            return self.at_conversation_distance
        if self.status is MatchStatus.NO_PERSON:
            return False
        return None


def person_azimuth(person: PersonObservation, geometry: MountGeometry) -> float:
    """The person's direction as the microphone sees it, in degrees (positive = right)."""
    angle = math.degrees(math.atan2(person.x_m - geometry.mic_x_m, person.z_m - geometry.mic_z_m))
    return wrap_degrees(angle - geometry.mic_yaw_deg)


def camera_azimuth(mic_azimuth_deg: float, geometry: MountGeometry) -> float:
    """A direction seen from the microphone, as an angle from the camera's forward axis.

    Exact when the microphone is at the camera; otherwise an approximation that
    is good for people further away than the distance between the two devices.
    """
    return wrap_degrees(mic_azimuth_deg + geometry.mic_yaw_deg)


def match_speaker(
    estimate: DirectionEstimate | None,
    people: Sequence[PersonObservation],
    geometry: MountGeometry,
    config: MatchConfig,
    at_conversation_distance: Callable[[PersonObservation], bool],
    camera_ready: bool = True,
) -> SpeakerMatch:
    seen = len(people)
    if estimate is None:
        return SpeakerMatch(MatchStatus.NO_DIRECTION, "ยังไม่มีค่าทิศทางเสียง", people_seen=seen)
    if not camera_ready:
        # An empty list then means "unknown", not "nobody there".
        return SpeakerMatch(MatchStatus.NO_CAMERA, "กล้องไม่ส่งผลตรวจจับ จึงจับคู่ผู้พูดไม่ได้")
    if not estimate.stable:
        return SpeakerMatch(
            MatchStatus.UNSTEADY,
            f"ทิศทางเสียงยังแกว่ง (กระจาย ±{estimate.spread_deg:.0f}°)", people_seen=seen,
        )
    if abs(camera_azimuth(estimate.azimuth_deg, geometry)) > geometry.camera_hfov_deg / 2:
        return SpeakerMatch(
            MatchStatus.OUT_OF_VIEW,
            f"เสียงมาจากทิศ {estimate.azimuth_deg:+.0f}° ซึ่งอยู่นอกมุมมองกล้อง", people_seen=seen,
        )
    measured = [person for person in people if person.measured]
    if not measured:
        if people:
            return SpeakerMatch(MatchStatus.UNMEASURED, "เห็นคนแต่กล้องวัดระยะไม่ได้", people_seen=seen)
        return SpeakerMatch(MatchStatus.NO_PERSON, "ไม่เห็นใครในทิศทางของเสียง", people_seen=0)

    ranked = sorted(
        ((angle_between(person_azimuth(p, geometry), estimate.azimuth_deg), p) for p in measured),
        key=lambda pair: pair[0],
    )
    best_error, best = ranked[0]
    if best_error > config.tolerance_deg:
        return SpeakerMatch(
            MatchStatus.NO_PERSON,
            f"ไม่มีคนในทิศ {estimate.azimuth_deg:+.0f}° (คนที่ใกล้ทิศนั้นที่สุดห่าง {best_error:.0f}°)",
            people_seen=seen,
        )
    if len(ranked) > 1 and ranked[1][0] - best_error < config.margin_deg:
        return SpeakerMatch(
            MatchStatus.AMBIGUOUS,
            f"มี {sum(1 for e, _ in ranked if e <= config.tolerance_deg)} คนอยู่ในทิศใกล้กัน แยกไม่ได้ว่าใครพูด",
            people_seen=seen,
        )
    close = at_conversation_distance(best)
    return SpeakerMatch(
        MatchStatus.MATCHED,
        "ผู้พูดอยู่ในระยะสนทนา" if close else "ผู้พูดอยู่ไกลกว่าระยะสนทนา",
        person=best,
        person_azimuth_deg=round(person_azimuth(best, geometry), 1),
        error_deg=round(best_error, 1),
        at_conversation_distance=close,
        people_seen=seen,
    )
