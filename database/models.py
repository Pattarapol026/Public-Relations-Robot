"""Plain data records returned by the repositories."""
from __future__ import annotations

from dataclasses import dataclass

MEDIA_VIDEO = "video"
MEDIA_IMAGE = "image"


@dataclass(frozen=True)
class MediaItem:
    id: int
    title: str
    description: str
    category: str
    media_type: str
    file_name: str
    original_name: str
    size_bytes: int
    enabled: bool
    sort_order: int


@dataclass(frozen=True)
class Announcement:
    id: int
    title: str
    body: str
    enabled: bool
    sort_order: int
    venue: str = ""  # kind of place it belongs to (services/venues.py); "" = every kind


@dataclass(frozen=True)
class KnowledgeItem:
    id: int
    category: str
    question: str
    answer: str
    keywords: tuple[str, ...]
    enabled: bool
    show_as_shortcut: bool
    expires_on: str = ""  # YYYY-MM-DD, last day it is used; "" = never expires
    venue: str = ""  # kind of place it belongs to (services/venues.py); "" = every kind
    question_en: str = ""  # the same entry in English; "" = Thai only
    answer_en: str = ""

    def expired(self, today: str) -> bool:
        """``today`` as YYYY-MM-DD."""
        return bool(self.expires_on) and self.expires_on < today


TRIGGER_CARD = "card"  # a printed marker card held up to the camera
TRIGGER_GESTURE = "gesture"  # a hand gesture

ACTION_SAY = "say"  # speak the response text and/or show the media
ACTION_FEEDBACK_UP = "feedback_up"  # record that the visitor liked the last answer
ACTION_FEEDBACK_DOWN = "feedback_down"
ACTION_STOP = "stop"  # stop the robot talking / listening


@dataclass(frozen=True)
class VisualTrigger:
    """What the robot does when it sees a particular card or gesture."""

    id: int
    kind: str
    code: str  # card number as text, or the gesture's name
    title: str
    action: str
    response_text: str
    media_id: int | None
    enabled: bool


@dataclass(frozen=True)
class Interaction:
    """One answered question."""

    id: int
    created_at: str
    question: str
    answer: str
    source: str
    language: str
    input_mode: str
    ai_error: str
    response_ms: int
    feedback: int
    resolved: bool


@dataclass(frozen=True)
class Schedule:
    """A public relations message the robot delivers by itself at a set time."""

    id: int
    title: str
    time_of_day: str  # "HH:MM"
    weekdays: str  # digits of the days it runs on, "0" = Monday
    message: str
    media_id: int | None
    enabled: bool
    last_run_date: str


@dataclass(frozen=True)
class ActivityLog:
    id: int
    created_at: str
    actor: str
    action: str
    detail: str
