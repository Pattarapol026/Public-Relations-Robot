"""Bit patterns of the ArUco DICT_4X4_50 markers used on the robot's cards.

Kept as data so the Admin screen can draw a card without importing OpenCV
into the GUI process. Each number is one marker's 4x4 inner cells, row by
row from the top-left, most significant bit first; 1 is a white cell. The
values were read from OpenCV 5.0 and tests/test_signs.py checks they still
match the dictionary OpenCV detects with.
"""
from __future__ import annotations

MARKER_COUNT = 50
CELLS = 4

PATTERNS: tuple[int, ...] = (
    0xB532, 0x0F9A, 0x332D, 0x9946, 0x549E, 0x79CD, 0x9E2E, 0xC4F2, 0xFEDA, 0xCF56,
    0xF991, 0x11A7, 0x0EB7, 0x2A0F, 0x24B1, 0x263E, 0x4665, 0x6600, 0x6C5E, 0x76AF,
    0x868B, 0xB02B, 0xCCD5, 0xDD82, 0xFE47, 0x9471, 0xACE4, 0xA554, 0x2123, 0x346F,
    0x4415, 0x57B2, 0x9ECF, 0xF0CB, 0x08AE, 0x0929, 0x1875, 0x04FF, 0x0DF6, 0x1C5A,
    0x1718, 0x2A28, 0x328C, 0x38B2, 0x24E8, 0x2EEB, 0x2D3F, 0x4B64, 0x502E, 0x5013,
)


def cells(marker_id: int) -> list[list[bool]]:
    """The marker as rows of cells, True for white."""
    pattern = PATTERNS[marker_id]
    return [
        [bool(pattern >> (CELLS * CELLS - 1 - (row * CELLS + column)) & 1) for column in range(CELLS)]
        for row in range(CELLS)
    ]
