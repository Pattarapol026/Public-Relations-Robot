"""Conversation state machine shared by the UI and the speech/AI pipeline."""
from __future__ import annotations

import logging
from enum import Enum

from PySide6.QtCore import QObject, Signal

logger = logging.getLogger(__name__)


class ConversationState(Enum):
    IDLE = "idle"
    LISTENING = "listening"
    THINKING = "thinking"
    SPEAKING = "speaking"
    ERROR = "error"


_S = ConversationState
ALLOWED_TRANSITIONS: dict[ConversationState, frozenset[ConversationState]] = {
    # IDLE -> THINKING covers touch shortcuts, which skip listening;
    # IDLE -> SPEAKING is the robot speaking first (a greeting).
    _S.IDLE: frozenset({_S.LISTENING, _S.THINKING, _S.SPEAKING}),
    _S.LISTENING: frozenset({_S.THINKING, _S.IDLE, _S.ERROR}),
    _S.THINKING: frozenset({_S.SPEAKING, _S.IDLE, _S.ERROR}),
    _S.SPEAKING: frozenset({_S.IDLE, _S.LISTENING, _S.ERROR}),
    _S.ERROR: frozenset({_S.IDLE}),
}


def can_transition(current: ConversationState, new: ConversationState) -> bool:
    return new in ALLOWED_TRANSITIONS[current]


class StateManager(QObject):
    state_changed = Signal(object, object)  # old, new

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._state = ConversationState.IDLE

    @property
    def state(self) -> ConversationState:
        return self._state

    def transition(self, new: ConversationState) -> bool:
        if new is self._state:
            return True
        if not can_transition(self._state, new):
            logger.warning("Rejected state transition %s -> %s", self._state.value, new.value)
            return False
        self._set(new)
        return True

    def reset(self) -> None:
        """Force IDLE from any state (cancel, idle timeout, error recovery)."""
        if self._state is not ConversationState.IDLE:
            self._set(ConversationState.IDLE)

    def _set(self, new: ConversationState) -> None:
        old, self._state = self._state, new
        logger.debug("State %s -> %s", old.value, new.value)
        self.state_changed.emit(old, new)
