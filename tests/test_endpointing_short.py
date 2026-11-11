"""A short phrase whose level dips between its syllables still counts as speech."""
from audio.endpointing import EndpointConfig, EndpointDecision, Endpointer

CONFIG = EndpointConfig(level_threshold=0.02, min_speech_s=0.25, silence_s=2.0, no_speech_timeout_s=10.0, max_seconds=30.0)
DT = 0.05


def feed(endpointer, pattern):
    """pattern: list of (level, seconds)."""
    decisions = []
    for level, seconds in pattern:
        for _ in range(round(seconds / DT)):
            decisions.append(endpointer.update(level, DT))
    return decisions


def test_two_short_syllables_with_a_gap_count_as_one_phrase():
    endpointer = Endpointer(CONFIG)
    feed(endpointer, [(0.001, 0.5), (0.2, 0.15), (0.001, 0.2), (0.2, 0.15)])  # "หก" ... "เจ็ด"
    assert endpointer.heard_speech


def test_bursts_far_apart_are_still_noise():
    endpointer = Endpointer(CONFIG)
    feed(endpointer, [(0.001, 0.5), (0.2, 0.15), (0.001, 1.0), (0.2, 0.15), (0.001, 1.0), (0.2, 0.15)])
    assert not endpointer.heard_speech


def test_a_short_phrase_is_finished_only_after_the_whole_silence():
    endpointer = Endpointer(CONFIG)
    decisions = feed(endpointer, [(0.001, 0.5), (0.2, 0.15), (0.001, 0.2), (0.2, 0.15), (0.001, 3.0)])
    assert EndpointDecision.FINISHED in decisions
    first = decisions.index(EndpointDecision.FINISHED)
    speech_end = round((0.5 + 0.15 + 0.2 + 0.15) / DT)
    assert (first - speech_end) * DT >= 1.9  # it waited about 2 s after the last sound
