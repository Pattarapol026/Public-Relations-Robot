"""Which kind of place a question is about, so it is answered from that place's data.

The Admin chooses the kind of place the robot stands in (``services/venues.py``).
A visitor may still ask about another one: at a college, "ผลงานสิ่งประดิษฐ์ทีมนี้
ทำงานยังไง" is about the invention competition. Every kind of place is scored
for the question, and the question goes to the best one only when it clearly
beats the place in use:

* its cue words in the question (``Venue.cues``), and
* how well its own knowledge-base entries answer the question.

A place is never chosen from its knowledge alone: "ห้องน้ำอยู่ไหน" matches
every place's toilets and must stay with the place in use. A place with no
entries of its own is never chosen either (nothing to answer from yet), and
"ทั่วไป" (general chat) is only ever the place in use.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Sequence

from ai import knowledge_service
from database.models import KnowledgeItem
from services import venues
from services.venues import Venue

CUE_WEIGHT = 2.0  # each cue word of the place in the question
KNOWLEDGE_CAP = 6.0  # the best entry's score counts up to this much
MIN_ROUTE_SCORE = 4.0  # e.g. two cue words, or one and an entry that answers it
MARGIN = 2.0  # how much more than the place in use another place must score


@dataclass(frozen=True)
class VenueScore:
    venue_id: str
    score: float
    cues: tuple[str, ...]
    knowledge: float  # the best entry's score (0 if none answers it)


@dataclass(frozen=True)
class Route:
    venue_id: str  # the place whose data answers the question
    active_id: str  # the place in use
    scores: tuple[VenueScore, ...] = field(default=())  # best first; for the log and Admin

    @property
    def switched(self) -> bool:
        return self.venue_id != self.active_id

    def summary(self) -> str:
        """For the log: "invention(6.0) education(0.0)"; never the question itself."""
        return " ".join(f"{s.venue_id}({s.score:.1f})" for s in self.scores[:3])


@dataclass(frozen=True)
class Place:
    venue: Venue
    name: str = ""  # the place's own name; "" = refer to it as venue.place

    @property
    def called(self) -> str:
        return self.name or self.venue.place


@dataclass(frozen=True)
class RoutedKnowledge:
    """What one question is answered from: the chosen place's entries and announcements."""

    items: list[KnowledgeItem]
    announcements: list[str]
    place: Place  # whose data it is
    home: Place  # where the robot stands (the place in use)
    route: Route | None = None  # None: routing is off

    @property
    def switched(self) -> bool:
        return self.place.venue.id != self.home.venue.id


def _knowledge_score(text: str, items: Sequence[KnowledgeItem]) -> float:
    """How well the place's best entry answers ``text`` (only an entry that covers it)."""
    for entry in knowledge_service.rank(text, items, limit=3):
        if not entry.partial:
            return min(KNOWLEDGE_CAP, entry.score)
    return 0.0


def score(text: str, venue: Venue, items: Sequence[KnowledgeItem]) -> VenueScore:
    found = tuple(venues.cues_in(text, venue))
    knowledge = _knowledge_score(text, items) if items else 0.0
    return VenueScore(venue.id, CUE_WEIGHT * len(found) + knowledge, found, knowledge)


def route(
    text: str, active_id: str, items_by_venue: Mapping[str, Sequence[KnowledgeItem]]
) -> Route:
    """The kind of place ``text`` is about. ``items_by_venue``: each place's own entries."""
    active = venues.get(active_id)
    scores = [score(text, active, items_by_venue.get(active.id, ()))]
    for venue in venues.VENUES:
        if venue.id == active.id or venue.general_chat or not items_by_venue.get(venue.id):
            continue
        if not venues.cues_in(text, venue):
            continue  # not from its knowledge alone (and it saves ranking every place)
        scores.append(score(text, venue, items_by_venue[venue.id]))
    scores.sort(key=lambda s: s.score, reverse=True)
    best, home = scores[0], next(s for s in scores if s.venue_id == active.id)
    tied = len(scores) > 1 and scores[1].score == best.score  # two other places alike: unsure
    chosen = active.id
    if (
        best.venue_id != active.id and not tied
        and best.score >= MIN_ROUTE_SCORE and best.score >= home.score + MARGIN
    ):
        chosen = best.venue_id
    return Route(chosen, active.id, tuple(scores))
