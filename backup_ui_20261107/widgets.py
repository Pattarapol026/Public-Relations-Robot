"""Small helpers and display texts shared by the screens."""
from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QSize
from PySide6.QtGui import QResizeEvent
from PySide6.QtWidgets import QFrame, QLabel, QPushButton, QVBoxLayout, QWidget

from robot.interfaces import RobotState, RobotStatus

ROBOT_STATE_LABELS = {
    RobotState.DISCONNECTED: "ไม่ได้เชื่อมต่อ",
    RobotState.IDLE: "พร้อม",
    RobotState.EXECUTING: "กำลังทำงาน",
    RobotState.ERROR: "ขัดข้อง",
}


def robot_state_text(status: RobotStatus) -> str:
    state = status.state if status.connected else RobotState.DISCONNECTED
    return ROBOT_STATE_LABELS[state]


def level_to_percent(level: float) -> int:
    """Map a microphone RMS level (speech is roughly 0.02–0.25) onto a 0–100 meter."""
    return max(0, min(100, int(level * 400)))


def set_style_property(widget: QWidget, name: str, value: str) -> None:
    """Set a dynamic property used by a QSS selector and re-apply the style.

    Re-applying the style is costly, so nothing is done when the value is unchanged
    (status displays call this on every camera update).
    """
    if widget.property(name) == value:
        return
    widget.setProperty(name, value)
    widget.style().unpolish(widget)
    widget.style().polish(widget)


def make_button(text: str, role: str, on_click: Callable[[], None]) -> QPushButton:
    button = QPushButton(text)
    button.setProperty("role", role)
    button.clicked.connect(lambda _checked=False: on_click())
    return button


class WrappedLabel(QLabel):
    """A word-wrapped label that always gets the height its text needs.

    A plain QLabel reports a minimum height for its text laid out much wider
    than it really is, so in a tight column the layout squeezes it and the text
    spills over its neighbours. This one asks for the height at its real width.
    """

    def __init__(self, text: str = "") -> None:
        super().__init__(text)
        self.setWordWrap(True)

    def minimumSizeHint(self) -> QSize:
        hint = super().minimumSizeHint()
        if self.width() > 0:
            hint.setHeight(max(hint.height(), self.heightForWidth(self.width())))
        return hint

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        if event.size().width() != event.oldSize().width():
            self.updateGeometry()  # a new width means a new required height


def make_label(text: str, role: str, word_wrap: bool = False) -> QLabel:
    label = QLabel(text)
    label.setProperty("role", role)
    label.setWordWrap(word_wrap)
    return label


def make_section(title: str, hint: str = "") -> tuple[QFrame, QVBoxLayout]:
    """A titled card that groups related controls on an Admin page."""
    frame = QFrame()
    frame.setProperty("role", "section")
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(16, 12, 16, 14)
    layout.setSpacing(8)
    layout.addWidget(make_label(title, "heading"))
    if hint:
        layout.addWidget(make_label(hint, "hint", word_wrap=True))
    return frame, layout
