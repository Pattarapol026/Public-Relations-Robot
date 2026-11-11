"""Polls the camera from the GUI thread and announces what visitors are doing.

Two distances matter ("Smart Welcome"):

* the **service area** — someone has come towards the robot and stayed: greet
  them and open the service menu, without listening yet;
* the **conversation distance** — they have stepped up to the robot: start
  listening.

Leaving the service area ends the visit.
"""
from __future__ import annotations

import logging
import math
import time

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtGui import QImage

from vision.interfaces import PersonObservation, SourceStatus, VisionSource
from vision.intent import AttentionTracker
from vision.presence import PresenceConfig, PresenceEvent, PresenceEventType, PresenceTracker
from vision.signs import Sign, SignTracker

logger = logging.getLogger(__name__)

POLL_MS = 50
# A snapshot older than this means the camera is not delivering: treat as "nobody seen".
STALE_SECONDS = 1.5


class VisionController(QObject):
    visitor_arrived = Signal(object)  # PresenceEvent: entered the service area
    visitor_engaged = Signal(object)  # PresenceEvent: came close enough to talk
    visitor_attentive = Signal(object)  # PresenceEvent: stopped in front of the camera (the cue to greet), once a visit
    visitor_left = Signal(object)  # PresenceEvent: left the service area
    changed = Signal()  # status, people count, distance or zone changed
    frame_changed = Signal()  # a new live picture and/or new person boxes are available
    sign_shown = Signal(object)  # Sign: a card or gesture was held up (once per showing)

    def __init__(
        self,
        source: VisionSource | None,
        area_config: PresenceConfig,
        talk_config: PresenceConfig,
        attention_still_s: float = 0.0,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._source = source
        # Who is worth greeting: a person who has stopped in front of the camera, not one passing by.
        self._attention = AttentionTracker(
            attention_still_s, area_config.engage_distance_m, area_config.lateral_limit_m
        )
        self._attentive_sent = False
        self._talk_config = talk_config
        self._area = PresenceTracker(area_config)
        self._talk = PresenceTracker(talk_config)
        self._present = False
        self._visit_started = 0.0
        self._last_in_area = 0.0
        self._closest: float | None = None
        self._last_timestamp: float | None = None
        self._last_view: tuple = ()
        self._people: tuple[PersonObservation, ...] = ()
        self._fresh = False
        self._frame_bytes: bytes | None = None
        self._frame = QImage()
        self._sign_tracker = SignTracker()
        self._last_sign: Sign | None = None

        self._timer = QTimer(self)
        self._timer.setInterval(POLL_MS)
        self._timer.timeout.connect(self._poll)
        if source is not None:
            source.start()
            self._timer.start()

    @property
    def status(self) -> SourceStatus:
        return self._source.status if self._source is not None else SourceStatus.DISABLED

    @property
    def detail(self) -> str:
        return self._source.detail if self._source is not None else ""

    @property
    def present(self) -> bool:
        """Someone is in the service area (or closer)."""
        return self._present

    @property
    def engaged(self) -> bool:
        """Someone is at conversation distance."""
        return self._talk.present

    @property
    def distance_m(self) -> float | None:
        return self._area.distance_m

    @property
    def people_count(self) -> int:
        return self._area.people_count

    @property
    def unmeasured_count(self) -> int:
        """People the camera sees but cannot measure the distance to right now."""
        return self._area.unmeasured_count

    @property
    def people(self) -> tuple[PersonObservation, ...]:
        """Everyone in the latest snapshot, including those too far to count as visitors."""
        return self._people

    @property
    def fresh(self) -> bool:
        """The camera is running and its latest result is current (not stalled)."""
        return self.status is SourceStatus.RUNNING and self._fresh

    @property
    def nearest_m(self) -> float | None:
        """Straight-line distance to the closest measured person in view, wherever they stand.

        People seen without depth are not counted here (their distance is not 0 m,
        it is unknown); ``unmeasured_nearby`` reports them.
        """
        measured = [person for person in self._people if person.measured]
        if not measured:
            return None
        return min(math.hypot(person.x_m, person.z_m) for person in measured)

    @property
    def unmeasured_nearby(self) -> bool:
        """Someone is in view whose distance cannot be measured (possibly very close)."""
        return any(not person.measured for person in self._people)

    @property
    def frame(self) -> QImage:
        """The live picture; a null image when there is none."""
        return self._frame

    @property
    def last_sign(self) -> Sign | None:
        """The most recent card or gesture recognised (for the Admin's camera tab)."""
        return self._last_sign

    def set_presence_configs(self, area_config: PresenceConfig, talk_config: PresenceConfig) -> None:
        """New distances and times from the Admin; they apply from the next camera reading."""
        self._area.set_config(area_config)
        self._talk.set_config(talk_config)
        self._talk_config = talk_config

    def is_at_conversation_distance(self, person: PersonObservation) -> bool:
        """Close and central enough to talk to (the rule the conversation tracker uses)."""
        config = self._talk_config
        return (
            config.min_distance_m <= person.z_m <= config.engage_distance_m
            and abs(person.x_m) <= config.lateral_limit_m
            and person.confidence >= config.min_confidence
        )

    def shutdown(self) -> None:
        self._timer.stop()
        if self._source is not None:
            self._source.stop()

    def _poll(self) -> None:
        now = time.monotonic()
        snapshot = self._source.latest()
        if snapshot is not None and now - snapshot.timestamp > STALE_SECONDS:
            snapshot = None
        self._fresh = snapshot is not None

        redraw = False
        if snapshot is None:
            # No data is fed as "nobody", so a visitor is released if the camera dies.
            self._update_presence((), now)
            if self._people:
                self._people = ()
                redraw = True
        elif snapshot.timestamp != self._last_timestamp:
            self._last_timestamp = snapshot.timestamp
            self._people = snapshot.people
            self._update_presence(snapshot.people, now)
            redraw = True
        # else: same frame as last poll, nothing new to learn

        frame_bytes = self._source.latest_frame()
        if frame_bytes is not self._frame_bytes:
            self._frame_bytes = frame_bytes
            self._frame = QImage.fromData(frame_bytes, "JPG") if frame_bytes else QImage()
            redraw = True
        if redraw:
            self.frame_changed.emit()

        for cycle in self._source.take_sign_cycles():
            for sign in self._sign_tracker.update(cycle, now):
                logger.info("Sign shown to the camera: %s %s", sign.kind, sign.code)
                self._last_sign = sign
                self.sign_shown.emit(sign)

        distance = self.distance_m
        view = (
            self.status, self.detail, self._present, self.engaged, self.people_count,
            None if distance is None else round(distance, 1), self._frame.isNull(),
            self._last_sign, self.unmeasured_count,
        )
        if view != self._last_view:
            self._last_view = view
            self.changed.emit()

    def _update_presence(self, people: tuple[PersonObservation, ...], now: float) -> None:
        self._area.update(people, now)
        talk_events = self._talk.update(people, now)
        area_limits = self._area.config
        self._attention.set_limits(area_limits.engage_distance_m, area_limits.lateral_limit_m)
        attentive = self._attention.update(people, now)
        distance = self._area.distance_m

        # Someone who walks straight up to the robot may reach conversation
        # distance before the slower "has stayed in the area" test passes;
        # either one makes them a visitor.
        tracked = self._area.present or self._talk.present
        area = self._area.config
        seen = (distance is not None and distance <= area.release_distance_m) or (
            self._present and self._area.unmeasured_count > 0
        )
        if tracked or seen:
            self._last_in_area = now
        # A visit ends by the service area's rule only. A visit begun at the
        # conversation spot used to end when that tracker gave up (1.5 s without
        # a reading), before the area tracker had confirmed the visitor; one short
        # gap in detection then ended the visit and the same person was greeted
        # again moments later.
        present = tracked or (self._present and now - self._last_in_area < area.leave_s)
        if present and not self._present:
            self._present = True
            self._visit_started = now
            self._closest = distance
            logger.info("Visitor arrived at %.1f m", distance or 0.0)
            self._attentive_sent = False
            self._attention.reset()
            self.visitor_arrived.emit(PresenceEvent(PresenceEventType.ARRIVED, distance_m=distance))
        if self._present and attentive and not self._attentive_sent:
            self._attentive_sent = True
            logger.info("Visitor has stopped in front of the camera at %.1f m", distance or 0.0)
            self.visitor_attentive.emit(PresenceEvent(PresenceEventType.ARRIVED, distance_m=distance))
        if self._present and distance is not None:
            self._closest = distance if self._closest is None else min(self._closest, distance)

        if any(event.type is PresenceEventType.ARRIVED for event in talk_events):
            logger.info("Visitor at conversation distance (%.1f m)", distance or 0.0)
            self.visitor_engaged.emit(PresenceEvent(PresenceEventType.ARRIVED, distance_m=distance))

        if self._present and not present:
            self._present = False
            self._attentive_sent = False
            self._attention.reset()
            duration = now - self._visit_started
            logger.info("Visitor left after %.0f s", duration)
            self.visitor_left.emit(
                PresenceEvent(PresenceEventType.LEFT, duration_s=duration, closest_m=self._closest)
            )
