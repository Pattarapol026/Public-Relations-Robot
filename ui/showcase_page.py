"""Public relations page: loops the enabled media and rotates announcements."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QFrame, QHBoxLayout, QPushButton, QVBoxLayout, QWidget

from app.content_controller import ContentController
from database.models import Announcement, MediaItem
from ui.media_view import MediaView
from ui.widgets import WrappedLabel, make_button, make_label

ERROR_SKIP_MS = 2500
ANNOUNCEMENT_MAX_WIDTH = 1000


class ShowcasePage(QWidget):
    def __init__(self, content: ContentController, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._content = content
        self._playlist: list[tuple[MediaItem, Path]] = []
        self._media_index = 0
        self._announcements: list[Announcement] = []
        self._announcement_index = 0
        self._active = False

        self._media_view = MediaView()
        self._media_view.finished.connect(lambda: self._step_media(1))
        self._media_view.failed.connect(self._on_media_failed)
        self._caption = WrappedLabel()  # a long title wraps instead of widening the page
        self._caption.setProperty("role", "media-caption")

        # Previous / pause / next form one segmented control.
        self._pause_button: QPushButton = make_button("หยุดชั่วคราว", "segment", self._toggle_pause)
        media_controls = QFrame()
        media_controls.setProperty("role", "segmented")
        controls_layout = QHBoxLayout(media_controls)
        controls_layout.setContentsMargins(4, 4, 4, 4)
        controls_layout.setSpacing(4)
        controls_layout.addWidget(make_button("ก่อนหน้า", "segment", lambda: self._step_media(-1)))
        controls_layout.addWidget(self._pause_button)
        controls_layout.addWidget(make_button("ถัดไป", "segment", lambda: self._step_media(1)))

        self._media_panel = QFrame()
        self._media_panel.setProperty("role", "media-card")
        media_layout = QVBoxLayout(self._media_panel)
        media_layout.setContentsMargins(10, 10, 10, 10)
        media_layout.setSpacing(10)
        media_layout.addWidget(self._media_view, 1)
        media_bar = QHBoxLayout()
        media_bar.setContentsMargins(6, 0, 0, 0)
        media_bar.setSpacing(16)
        media_bar.addWidget(self._caption, 1)
        media_bar.addWidget(media_controls)
        media_layout.addLayout(media_bar)

        self._announcement_card = QFrame()
        self._announcement_card.setProperty("role", "card")
        card_layout = QVBoxLayout(self._announcement_card)
        card_layout.setContentsMargins(32, 20, 32, 20)
        # The text keeps a comfortable line length however wide the card is.
        text_column = QWidget()
        text_column.setMaximumWidth(ANNOUNCEMENT_MAX_WIDTH)
        text_layout = QVBoxLayout(text_column)
        text_layout.setContentsMargins(0, 0, 0, 0)
        text_layout.setSpacing(10)
        self._announcement_eyebrow = make_label("", "eyebrow")
        self._announcement_eyebrow.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._announcement_title = WrappedLabel()
        self._announcement_title.setProperty("role", "announcement")
        self._announcement_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._announcement_body = WrappedLabel()
        self._announcement_body.setProperty("role", "announcement-body")
        self._announcement_body.setAlignment(Qt.AlignmentFlag.AlignCenter)
        text_layout.addWidget(self._announcement_eyebrow)
        text_layout.addWidget(self._announcement_title)
        text_layout.addWidget(self._announcement_body)
        # Centred by stretches, not by alignment: an aligned column would be only
        # as wide as its size hint and its wrapped text would be cut off.
        centred = QHBoxLayout()
        centred.addStretch(1)
        centred.addWidget(text_column, 100)
        centred.addStretch(1)
        card_layout.addStretch(1)
        card_layout.addLayout(centred)
        card_layout.addStretch(1)

        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(16)
        self._layout.addWidget(self._media_panel, 3)
        self._layout.addWidget(self._announcement_card, 1)

        self._announcement_timer = QTimer(self)
        self._announcement_timer.timeout.connect(self._next_announcement)
        self._skip_timer = QTimer(self)
        self._skip_timer.setSingleShot(True)
        self._skip_timer.timeout.connect(lambda: self._step_media(1))

        content.content_changed.connect(self.reload)
        self.reload()

    def has_media(self) -> bool:
        return bool(self._playlist)

    def has_content(self) -> bool:
        """True if there is anything to show: enabled media or enabled announcements."""
        return bool(self._playlist) or bool(self._announcements)

    def activate(self) -> None:
        """Start playing; called when the page becomes visible."""
        if self._active:
            return
        self._active = True
        self._announcement_timer.start()
        self._play_current()

    def deactivate(self) -> None:
        """Stop sound and timers; called when the visitor leaves the page."""
        self._active = False
        self._announcement_timer.stop()
        self._skip_timer.stop()
        self._media_view.stop()

    def play_item(self, media_id: int) -> None:
        for index, (item, _path) in enumerate(self._playlist):
            if item.id == media_id:
                self._media_index = index
                break
        self._active = False
        self.activate()

    def reload(self) -> None:
        current_id = self._playlist[self._media_index][0].id if self._playlist else None
        self._playlist = self._content.playlist()
        self._media_index = next(
            (i for i, (item, _p) in enumerate(self._playlist) if item.id == current_id), 0
        )
        self._announcements = self._content.active_announcements()
        self._announcement_index = 0
        self._media_view.set_image_seconds(self._content.image_seconds())
        self._announcement_timer.setInterval(self._content.announcement_seconds() * 1000)

        self._media_panel.setVisible(bool(self._playlist))
        self._announcement_card.setVisible(bool(self._announcements) or not self._playlist)
        self._show_announcement()
        if self._active:
            self._play_current()

    # --- media ---

    def _play_current(self) -> None:
        self._skip_timer.stop()
        self._pause_button.setText("หยุดชั่วคราว")
        if not self._playlist:
            self._media_view.stop()
            return
        item, path = self._playlist[self._media_index]
        position = f"{self._media_index + 1}/{len(self._playlist)}"
        self._caption.setText(f"{item.title}  ({position})")
        self._media_view.show_media(path, item.media_type)

    def _step_media(self, step: int) -> None:
        if not self._playlist:
            return
        self._media_index = (self._media_index + step) % len(self._playlist)
        if self._active:
            self._play_current()

    def _toggle_pause(self) -> None:
        if self._media_view.is_paused():
            self._media_view.resume()
            self._pause_button.setText("หยุดชั่วคราว")
        else:
            self._media_view.pause()
            self._pause_button.setText("เล่นต่อ")

    def _on_media_failed(self, message: str) -> None:
        self._media_view.show_placeholder(message)
        if self._active and len(self._playlist) > 1:
            self._skip_timer.start(ERROR_SKIP_MS)

    # --- announcements ---

    def _next_announcement(self) -> None:
        if self._announcements:
            self._announcement_index = (self._announcement_index + 1) % len(self._announcements)
            self._show_announcement()

    def _show_announcement(self) -> None:
        if not self._announcements:
            self._announcement_eyebrow.setText("ประชาสัมพันธ์")
            self._announcement_title.setText("ยังไม่มีประกาศ")
            self._announcement_body.setText("")
            self._announcement_body.hide()
            return
        item = self._announcements[self._announcement_index]
        count = len(self._announcements)
        self._announcement_eyebrow.setText(
            f"ประกาศ  {self._announcement_index + 1}/{count}" if count > 1 else "ประกาศ"
        )
        self._announcement_title.setText(item.title)
        self._announcement_body.setText(item.body)
        self._announcement_body.setVisible(bool(item.body))
