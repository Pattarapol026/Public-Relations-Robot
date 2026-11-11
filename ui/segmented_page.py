"""A page made of sub-pages, switched by a row of pill buttons (so the sidebar stays short)."""
from __future__ import annotations

from typing import Callable

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)


class SegmentedPage(QWidget):
    """Sub-pages under one sidebar entry.

    ``ask_discard(widget)`` is asked before leaving a sub-page that has unsaved edits and
    returns False to stay. The page itself answers ``has_unsaved_changes`` / ``discard_changes``
    for the sub-pages that have them, so it can stand in the Admin's list of editable pages.
    """

    sub_page_changed = Signal(int)

    def __init__(self, ask_discard: Callable[[QWidget], bool] | None = None) -> None:
        super().__init__()
        self._ask_discard = ask_discard
        self._stack = QStackedWidget()
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._buttons: list[QPushButton] = []
        self._widgets: list[QWidget] = []
        self._editables: list[QWidget] = []  # what holds the edits (inside a scroll area, perhaps)
        self._descriptions: list[str] = []
        self._on_show: list[Callable[[], None] | None] = []

        self._bar = QFrame()
        self._bar.setProperty("role", "segments")
        self._bar_layout = QHBoxLayout(self._bar)
        self._bar_layout.setContentsMargins(4, 4, 4, 4)
        self._bar_layout.setSpacing(4)
        self._bar_layout.addStretch(1)
        row = QHBoxLayout()
        row.addWidget(self._bar)
        row.addStretch(1)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        layout.addLayout(row)
        layout.addWidget(self._stack, 1)
        self._group.idClicked.connect(self._on_clicked)

    def add(
        self, title: str, widget: QWidget, description: str = "",
        on_show: Callable[[], None] | None = None, editable: QWidget | None = None,
    ) -> int:
        index = self._stack.addWidget(widget)
        button = QPushButton(title)
        button.setProperty("role", "segment")
        button.setCheckable(True)
        self._group.addButton(button, index)
        self._bar_layout.insertWidget(self._bar_layout.count() - 1, button)
        self._buttons.append(button)
        self._widgets.append(widget)
        self._editables.append(editable if editable is not None else widget)
        self._descriptions.append(description)
        self._on_show.append(on_show)
        if index == 0:
            button.setChecked(True)
        return index

    @property
    def current_index(self) -> int:
        return self._stack.currentIndex()

    @property
    def current_description(self) -> str:
        return self._descriptions[self._stack.currentIndex()]

    def show_sub_page(self, index: int) -> None:
        self._buttons[index].setChecked(True)
        self._stack.setCurrentIndex(index)
        self.refresh_current()
        self.sub_page_changed.emit(index)

    def refresh_current(self) -> None:
        callback = self._on_show[self._stack.currentIndex()]
        if callback is not None:
            callback()

    def _on_clicked(self, index: int) -> None:
        current = self._stack.currentIndex()
        if index == current:
            return
        if self._ask_discard is not None and not self._ask_discard(self._editables[current]):
            self._buttons[current].setChecked(True)
            return
        self.show_sub_page(index)

    # --- the Admin's editable-page protocol ---

    def save_changes(self) -> bool:
        """Save every sub-page that has edits; False (stay) if any of them cannot be saved."""
        for index, widget in enumerate(self._editables):
            if getattr(widget, "has_unsaved_changes", lambda: False)():
                if not getattr(widget, "save_changes", lambda: False)():
                    self.show_sub_page(index)  # show the one whose message explains why
                    return False
        return True

    def has_unsaved_changes(self) -> bool:
        return any(
            getattr(widget, "has_unsaved_changes", lambda: False)() for widget in self._editables
        )

    def discard_changes(self) -> None:
        for widget in self._editables:
            discard = getattr(widget, "discard_changes", None)
            if discard is not None:
                discard()
