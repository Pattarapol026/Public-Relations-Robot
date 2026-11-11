"""Decides when a visitor has finished speaking, from microphone levels alone."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class EndpointDecision(Enum):
    CONTINUE = "continue"
    FINISHED = "finished"  # speech was heard, then enough silence
    NO_SPEECH = "no_speech"  # nobody spoke within the waiting time
    TOO_LONG = "too_long"  # reached the recording limit; transcribe what we have
    PAUSED = "paused"  # a pause long enough that they may be done: start transcribing, keep listening
    RESUMED = "resumed"  # they went on speaking after a PAUSED: what was transcribed is out of date


@dataclass(frozen=True)
class EndpointConfig:
    level_threshold: float = 0.02  # RMS above this counts as speech
    min_speech_s: float = 0.25  # shorter bursts are treated as noise (a click, a cough)
    silence_s: float = 0.5  # silence after speech that ends the utterance
    no_speech_timeout_s: float = 6.0
    max_seconds: float = 20.0
    # A pause this long (shorter than ``silence_s``) starts a trial transcription while the microphone
    # stays open. 0 = off. See ``Endpointer.update`` and ``VoiceController``.
    early_silence_s: float = 0.0


# The pause after which the robot starts working out what was said, while it keeps listening
# (see ``VoiceController``). Hearing out a pause costs nothing: the transcription takes seconds anyway.
EARLY_SILENCE_S = 0.25
EARLY_PAUSE_GROWTH = 1.6
NOISE_MARGIN = 4.0  # speech is at least this many times the room's noise level
MIN_ADAPTIVE_THRESHOLD = 0.006  # never treat anything quieter than this as speech
# The level dips between the syllables of a short phrase ("หก" - "เจ็ด": the stop before the second word), so the sound of a
# phrase arrives as bursts a fraction of a second long. They count together while the gaps between them stay under this
# (11 Nov 2026: "หกเจ็ด" was heard by the level bar but never counted as speech, because each burst was under min_speech_s).
SPEECH_GAP_S = 0.4
NOISE_SETTLE_S = 0.3  # the noise level is measured over this long before it is used
_NOISE_RISE = 0.01  # how fast the noise estimate follows louder input (per update)


class Endpointer:
    """Feed it ``(level, seconds_since_last_update)`` while recording.

    A built-in laptop microphone without automatic gain can stay below the
    configured threshold even when someone talks to it. So the threshold is
    lowered towards a few times the measured room noise when the room is
    quiet; it is never raised above the configured value.
    """

    def __init__(self, config: EndpointConfig) -> None:
        self._config = config
        self.reset()

    def reset(self) -> None:
        self._elapsed = 0.0
        self._speech = 0.0
        self._silence = 0.0
        self._heard_speech = False
        self._noise: float | None = None
        self._pause_reported = False  # a PAUSED was returned and no RESUMED since
        self._early = self._config.early_silence_s  # grows each time a pause turns out not to be the end
        self._resume_run = 0.0  # seconds of unbroken speech since then

    @property
    def heard_speech(self) -> bool:
        return self._heard_speech

    @property
    def silence_seconds(self) -> float:
        """How long it has been quiet since the last speech."""
        return self._silence

    @property
    def threshold(self) -> float:
        configured = self._config.level_threshold
        if self._noise is None or self._elapsed < NOISE_SETTLE_S:
            return configured
        return min(configured, max(self._noise * NOISE_MARGIN, MIN_ADAPTIVE_THRESHOLD))

    def update(self, level: float, dt: float) -> EndpointDecision:
        config = self._config
        self._elapsed += dt
        # Follows quiet input at once and loud input only slowly, so it tracks
        # the room rather than the voice.
        if self._noise is None or level < self._noise:
            self._noise = level
        else:
            self._noise += (level - self._noise) * _NOISE_RISE
        resumed = False
        if level >= self.threshold:
            self._speech += dt
            self._silence = 0.0
            if self._speech >= config.min_speech_s:
                self._heard_speech = True
            if self._pause_reported:
                # Real speech again, not a click: it has to last as long as speech does to start with.
                self._resume_run += dt
                if self._resume_run >= config.min_speech_s:
                    self._pause_reported = False
                    self._resume_run = 0.0
                    resumed = True
                    # Someone who pauses to think pauses again: working out what they said at every
                    # pause costs seconds of the computer each time, and holds up the answer that
                    # counts. Each pause that was not the end makes the next one count later.
                    if self._early > 0.0:
                        self._early = min(self._early * EARLY_PAUSE_GROWTH, config.silence_s * 0.85)
        else:
            self._silence += dt
            self._resume_run = 0.0
            if not self._heard_speech and self._silence >= SPEECH_GAP_S:
                self._speech = 0.0  # bursts further apart than a gap inside a phrase are separate noises, not one word

        if self._elapsed >= config.max_seconds:
            return EndpointDecision.TOO_LONG if self._heard_speech else EndpointDecision.NO_SPEECH
        if self._heard_speech and self._silence >= config.silence_s:
            return EndpointDecision.FINISHED
        if not self._heard_speech and self._elapsed >= config.no_speech_timeout_s:
            return EndpointDecision.NO_SPEECH
        if resumed:
            return EndpointDecision.RESUMED
        early = self._early
        if (self._heard_speech and 0.0 < early < config.silence_s
                and self._silence >= early and not self._pause_reported):
            self._pause_reported = True
            return EndpointDecision.PAUSED
        return EndpointDecision.CONTINUE
