"""Draws a printable marker card: the ArUco marker with its number and title.

The marker is drawn from the bit patterns in ``vision/aruco_4x4_50.py``, so the
GUI process never imports OpenCV.
"""
from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QImage, QPainter

from vision.aruco_4x4_50 import CELLS, cells

CARD_WIDTH, CARD_HEIGHT = 1240, 1748  # A6 at 300 dpi
MARKER_SIDE = 900  # including its black border
QUIET_ZONE = 110  # white margin the detector needs around the marker


def render_card(marker_id: int, title: str, font_family: str) -> QImage:
    image = QImage(CARD_WIDTH, CARD_HEIGHT, QImage.Format.Format_RGB32)
    image.fill(QColor("white"))
    painter = QPainter(image)

    left = (CARD_WIDTH - MARKER_SIDE) / 2
    top = QUIET_ZONE + 60
    cell = MARKER_SIDE / (CELLS + 2)  # one-cell black border on every side
    painter.fillRect(QRectF(left, top, MARKER_SIDE, MARKER_SIDE), QColor("black"))
    for row, line in enumerate(cells(marker_id)):
        for column, white in enumerate(line):
            if white:
                # +0.5 px overlap so neighbouring white cells leave no hairline between them.
                painter.fillRect(
                    QRectF(left + (column + 1) * cell, top + (row + 1) * cell, cell + 0.5, cell + 0.5),
                    QColor("white"),
                )

    text_top = top + MARKER_SIDE + QUIET_ZONE
    font = QFont(font_family)
    font.setPixelSize(96)
    font.setBold(True)
    painter.setFont(font)
    painter.setPen(QColor("#0f172a"))
    painter.drawText(
        QRectF(80, text_top, CARD_WIDTH - 160, 330),
        Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap,
        title,
    )
    font.setPixelSize(44)
    font.setBold(False)
    painter.setFont(font)
    painter.setPen(QColor("#64748b"))
    painter.drawText(
        QRectF(80, CARD_HEIGHT - 150, CARD_WIDTH - 160, 80),
        Qt.AlignmentFlag.AlignCenter,
        f"การ์ดหมายเลข {marker_id} · ยกให้หุ่นยนต์ดู",
    )
    painter.end()
    return image
