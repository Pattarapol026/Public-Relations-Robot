"""A small data-entry dialog built from a list of field descriptions."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLineEdit,
    QPlainTextEdit,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ui.widgets import make_button, make_label

LINE = "line"
TEXT = "text"
CHECK = "check"
CHOICE = "choice"


@dataclass(frozen=True)
class Field:
    key: str
    label: str
    kind: str = LINE
    value: Any = ""
    max_length: int = 500
    options: tuple[tuple[str, Any], ...] = ()  # CHOICE only: (label shown, value returned)


class FormDialog(QDialog):
    """Calls ``submit(values)`` on OK; a raised ``error_type`` keeps the dialog open."""

    def __init__(
        self,
        title: str,
        fields: list[Field],
        submit: Callable[[dict[str, Any]], None],
        error_type: type[Exception],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._submit = submit
        self._error_type = error_type
        self._editors: dict[str, QWidget] = {}
        self.setWindowTitle(title)
        self.setModal(True)
        self.setMinimumWidth(860)

        form = QFormLayout()
        form.setVerticalSpacing(10)
        form.setHorizontalSpacing(16)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        for field in fields:
            editor = self._make_editor(field)
            self._editors[field.key] = editor
            form.addRow("" if field.kind == CHECK else field.label, editor)

        self._error = make_label("", "error", word_wrap=True)
        buttons = QHBoxLayout()
        buttons.addWidget(make_button("ยกเลิก", "secondary", self.reject))
        buttons.addWidget(make_button("บันทึก", "primary", self._on_save))

        # A long form scrolls instead of growing taller than the screen.
        container = QWidget()
        container.setLayout(form)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(container)
        screen = self.screen()
        available = screen.availableGeometry().height() if screen is not None else 720
        scroll.setMinimumHeight(min(container.sizeHint().height() + 4, max(240, available - 280)))

        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.addWidget(make_label(title, "heading"))
        layout.addWidget(scroll, 1)
        layout.addWidget(self._error)
        layout.addLayout(buttons)

    def values(self) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, editor in self._editors.items():
            if isinstance(editor, QCheckBox):
                result[key] = editor.isChecked()
            elif isinstance(editor, QComboBox):
                result[key] = editor.currentData()
            elif isinstance(editor, QPlainTextEdit):
                result[key] = editor.toPlainText()
            else:
                result[key] = editor.text()
        return result

    @staticmethod
    def _make_editor(field: Field) -> QWidget:
        if field.kind == CHECK:
            editor = QCheckBox(field.label)
            editor.setChecked(bool(field.value))
            return editor
        if field.kind == CHOICE:
            editor = QComboBox()
            for label, value in field.options:
                editor.addItem(label, value)
            editor.setCurrentIndex(max(0, editor.findData(field.value)))
            return editor
        if field.kind == TEXT:
            editor = QPlainTextEdit(str(field.value))
            editor.setFixedHeight(96)
            editor.setTabChangesFocus(True)
            return editor
        editor = QLineEdit(str(field.value))
        editor.setMaxLength(field.max_length)
        return editor

    def _on_save(self) -> None:
        try:
            self._submit(self.values())
        except self._error_type as exc:
            self._error.setText(str(exc))
            return
        self.accept()
