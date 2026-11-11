"""Where the voice comes from, and which visitor it belongs to (no robot movement).

Combines a ``DirectionSource`` (a microphone that measures direction), the
existing speech detection (``VoiceController.speech_active``), the robot's own
speech (readings are ignored while it talks) and the people the camera sees.
Without a direction-capable microphone it says so and changes nothing.
"""
from __future__ import annotations

import logging
import time

from PySide6.QtCore import QObject, QTimer, Signal

from app.speech_controller import SpeechController
from app.vision_controller import VisionController
from app.voice_controller import VoiceController
from audio.direction import DirectionEstimate, DirectionSource, DirectionTracker, TrackerConfig
from services.speaker_matching import MatchConfig, MountGeometry, SpeakerMatch, match_speaker
from vision.interfaces import SourceStatus

logger = logging.getLogger(__name__)

POLL_MS = 100


class SpeakerController(QObject):
    changed = Signal()

    def __init__(
        self,
        source: DirectionSource,
        vision: VisionController,
        voice: VoiceController,
        speech: SpeechController | None,
        geometry: MountGeometry,
        match_config: MatchConfig | None = None,
        tracker_config: TrackerConfig | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._source = source
        self._vision = vision
        self._voice = voice
        self._speech = speech
        self._geometry = geometry
        self._match_config = match_config or MatchConfig()
        self._tracker = DirectionTracker(tracker_config)
        self._was_listening = False
        # Set by the app for a board whose direction must be calibrated before it may veto anything.
        self.veto_enabled = None
        self.board = None  # the DirectionController, if the microphone is a calibratable array
        self._last_estimate: DirectionEstimate | None = None
        self._timer = QTimer(self)
        self._timer.setInterval(POLL_MS)
        self._timer.timeout.connect(self._poll)
        if source.available:
            source.start()
            self._timer.start()
            # Only a capable microphone may veto an utterance; otherwise voice works as before.
            voice.speaker_check = self.utterance_from_visitor

    # --- state for the dashboard ---

    @property
    def available(self) -> bool:
        return self._source.available

    @property
    def source_text(self) -> str:
        return self._source.status_text

    @property
    def estimate(self) -> DirectionEstimate | None:
        """The latest direction (kept after the speaker stops, for display)."""
        return self._last_estimate

    def match(self) -> SpeakerMatch:
        return match_speaker(
            self._tracker.estimate() if self._source.available else None,
            self._vision.people,
            self._geometry,
            self._match_config,
            self._vision.is_at_conversation_distance,
            camera_ready=self._vision.status is SourceStatus.RUNNING and self._vision.fresh,
        )

    def utterance_from_visitor(self) -> bool | None:
        """Asked when an utterance ends: False only when confident it was not the visitor."""
        if self.veto_enabled is not None and not self.veto_enabled():
            return None  # not calibrated or not switched on: answer as usual
        result = self.match()
        logger.info("Speaker check: %s — %s", result.status.value, result.reason)
        return result.from_visitor

    def shutdown(self) -> None:
        self._timer.stop()
        self._source.stop()

    # --- internals ---

    def _poll(self) -> None:
        listening = self._voice.is_listening
        if listening and not self._was_listening:
            self._tracker.reset()  # a new turn: earlier voices do not count
        self._was_listening = listening
        readings = self._source.read()
        if not readings:
            return
        speech_active = self._voice.speech_active
        robot_speaking = self._speech is not None and self._speech.is_speaking
        for reading in readings:
            self._tracker.add(reading, speech_active, robot_speaking)
        estimate = self._tracker.estimate(time.monotonic())
        if estimate is not None and estimate != self._last_estimate:
            self._last_estimate = estimate
            self.changed.emit()
