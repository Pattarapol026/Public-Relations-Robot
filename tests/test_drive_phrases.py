"""Spoken moves for the real robot (10 Nov 2026): "หันซ้าย 70 องศา", "เดินหน้า 20 เซน", "กลับมาที่เดิม"."""
from __future__ import annotations

import pytest

from services.drive_phrases import DEFAULT_MOVE_CM, DEFAULT_TURN_DEG, KIND_DRIVE, KIND_RETURN, parse


@pytest.mark.parametrize("text, direction, amount", [
    ("หันซ้าย 70 องศา", "left", 70), ("หันขวา 90 องศาครับ", "right", 90), ("เดินหน้า 20 เซน", "forward", 20),
    ("เดินหน้า 20 เซนติเมตรครับ", "forward", 20), ("ถอยหลัง 1 เมตร", "backward", 100), ("ถอยหลังครึ่งเมตร", "backward", 50),
    ("ไปข้างหน้า 50 เซน", "forward", 50), ("หันซ้ายเจ็ดสิบองศา", "left", 70), ("เดินหน้ายี่สิบเซน", "forward", 20),
    ("turn left 90 degrees", "left", 90), ("go forward 30 cm", "forward", 30), ("หันซ้าย 70", "left", 70),
])
def test_a_move_with_its_amount(text, direction, amount):
    intent = parse(text)
    assert intent is not None and intent.kind == KIND_DRIVE
    assert (intent.direction, intent.amount, intent.amount_said) == (direction, amount, True)


@pytest.mark.parametrize("text, direction, default", [
    ("หันซ้าย", "left", DEFAULT_TURN_DEG), ("หันขวาหน่อยครับ", "right", DEFAULT_TURN_DEG),
    ("เดินหน้า", "forward", DEFAULT_MOVE_CM), ("ถอยหลัง", "backward", DEFAULT_MOVE_CM), ("back up", "backward", DEFAULT_MOVE_CM),
])
def test_a_move_without_an_amount_gets_the_default(text, direction, default):
    intent = parse(text)
    assert intent is not None and intent.amount is None and intent.direction == direction
    assert intent.effective_amount == default
    assert (DEFAULT_TURN_DEG, DEFAULT_MOVE_CM) == (50, 30)


@pytest.mark.parametrize("text", [
    "กลับมาที่เดิม", "หันกลับมาที่เดิม", "กลับตำแหน่งเดิม", "กลับไปที่เดิมครับ", "กลับจุดเริ่มต้น", "return to the start",
])
def test_back_to_where_it_was(text):
    intent = parse(text)
    assert intent is not None and intent.kind == KIND_RETURN


@pytest.mark.parametrize("text", [
    "ห้องน้ำอยู่ทางซ้ายไหม", "ภาคใต้มีกี่ชิ้น", "ขอนแก่นส่ง 67 ชิ้น", "หันซ้าย 20 เซน", "เดินหน้า 70 องศา", "หันกลับ",
    "หันซ้ายแล้วเดินหน้า", "ทางไปห้องน้ำเดินหน้าแล้วเลี้ยวซ้าย", "อาคาร 9 อยู่ทางไหน",
])
def test_a_question_or_a_mixed_up_sentence_is_not_a_move(text):
    assert parse(text) is None


def test_the_way_back_undoes_the_moves_last_first_and_adds_up_the_ones_in_a_line():
    from app.hardware_controller import HardwareController
    from services.hardware_link import HardwareLink, HardwareLinkConfig

    controller = HardwareController(HardwareLink(HardwareLinkConfig(host="127.0.0.1", stop_payload="stop")))
    for move in (("forward", 20), ("forward", 10), ("left", 90), ("backward", 5), ("right", 30)):
        controller._remember(*move)
    assert controller.way_back() == [("left", 30), ("forward", 5), ("right", 90), ("backward", 30)]
    controller.forget_position()
    assert controller.way_back() == []
    controller._remember("forward", 250)  # longer than one command may be: split
    assert controller.way_back() == [("backward", 100), ("backward", 100), ("backward", 50)]


def test_the_thai_model_s_spellings_of_han_are_understood():
    from services import drive_phrases

    for said, direction in (("ถันขวา", "right"), ("หั่นซ้าย 70 องศา", "left")):
        intent = drive_phrases.parse(said)
        assert intent is not None and intent.direction == direction, said
