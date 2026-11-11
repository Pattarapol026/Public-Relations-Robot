"""Standing still in front of the camera is what makes the robot greet: the judgement itself."""
from __future__ import annotations

from vision.intent import AttentionTracker
from vision.interfaces import PersonObservation


def person(x=0.0, z=1.5, confidence=0.9):
    return PersonObservation(x_m=x, z_m=z, confidence=confidence)


def run(tracker, positions, start=0.0, step=0.1):
    """Feed one reading per step; returns the answers."""
    answers = []
    for i, p in enumerate(positions):
        answers.append(tracker.update([] if p is None else [p], start + i * step))
    return answers


def test_someone_who_stands_still_is_attentive_after_the_wait_and_not_before():
    tracker = AttentionTracker(still_s=1.0, max_distance_m=3.0, lateral_limit_m=1.0)
    answers = run(tracker, [person(0.05, 1.5)] * 20)
    assert not any(answers[:9])  # not for the first second
    assert answers[-1]


def test_walking_past_is_never_attentive():
    tracker = AttentionTracker(still_s=1.0, max_distance_m=3.0, lateral_limit_m=1.0)
    answers = run(tracker, [person(-0.9 + 0.09 * i, 1.5) for i in range(20)])
    assert not any(answers)


def test_walking_towards_the_camera_is_not_attentive_until_they_stop():
    tracker = AttentionTracker(still_s=1.0, max_distance_m=3.0, lateral_limit_m=1.0)
    approach = [person(0.0, 3.0 - 0.07 * i) for i in range(20)]  # 1.4 m in two seconds
    assert not any(run(tracker, approach))
    stopped = run(tracker, [person(0.0, 1.6)] * 15, start=2.0)
    assert stopped[-1]


def test_small_sway_is_standing_still_and_far_off_to_the_side_or_too_far_is_not_in_front():
    tracker = AttentionTracker(still_s=1.0, max_distance_m=3.0, lateral_limit_m=1.0)
    sway = [person(0.08 * ((-1) ** i), 1.5 + 0.05 * ((-1) ** i)) for i in range(20)]
    assert run(tracker, sway)[-1]
    tracker.reset()
    assert not any(run(AttentionTracker(1.0, 3.0, 1.0), [person(1.6, 1.5)] * 20))  # beside it
    assert not any(run(AttentionTracker(1.0, 3.0, 1.0), [person(0.0, 4.5)] * 20))  # too far


def test_flickering_in_and_out_of_view_is_not_standing_there():
    tracker = AttentionTracker(still_s=1.0, max_distance_m=3.0, lateral_limit_m=1.0)
    flicker = [person() if i % 3 == 0 else None for i in range(30)]  # seen one reading in three
    assert not any(run(tracker, flicker))


def test_a_doubtful_detection_or_one_without_distance_does_not_count_and_zero_means_always():
    tracker = AttentionTracker(still_s=1.0, max_distance_m=3.0, lateral_limit_m=1.0)
    assert not any(run(tracker, [person(confidence=0.2)] * 20))
    assert not any(run(AttentionTracker(1.0, 3.0, 1.0), [person(z=0.0)] * 20))  # no depth: unknown distance
    assert AttentionTracker(0.0, 3.0, 1.0).update([], 0.0) is True  # switched off: arriving is enough
