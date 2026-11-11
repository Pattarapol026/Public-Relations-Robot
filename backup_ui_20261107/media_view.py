"""Shows one image or plays one video; reports when it is done."""
from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QImageReader, QPixmap, QResizeEvent
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import QLabel, QStackedLayout, QWidget

from database.models import MEDIA_VIDEO

logger = logging.getLogger(__name__)

# Large photos are decoded at most this big; the screen never needs more.
MAX_DECODE_SIDE = 3840


class MediaView(QWidget):
    finished = Signal()  # the video ended or the image's display time elapsed
    failed = Signal(str)

    def __init__(self, image_seconds: int = 8, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._pixmap = QPixmap()
        self._showing_video = False
        self._paused = False

        self._placeholder = QLabel()
        self._placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._placeholder.setWordWrap(True)
        self._placeholder.setProperty("role", "media-placeholder")
        self._image = QLabel()
        self._image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._image.setMinimumSize(1, 1)
        self._image.setProperty("role", "media-surface")
        self._video = QVideoWidget()

        self._stack = QStackedLayout(self)
        self._stack.setContentsMargins(0, 0, 0, 0)
        for widget in (self._placeholder, self._image, self._video):
            self._stack.addWidget(widget)

        self._audio = QAudioOutput(self)
        self._player = QMediaPlayer(self)
        self._player.setAudioOutput(self._audio)
        self._player.setVideoOutput(self._video)
        self._player.mediaStatusChanged.connect(self._on_media_status)
        self._player.errorOccurred.connect(self._on_player_error)

        self._image_timer = QTimer(self)
        self._image_timer.setSingleShot(True)
        self._image_timer.timeout.connect(self.finished)
        self.set_image_seconds(image_seconds)

    def set_image_seconds(self, seconds: int) -> None:
        self._image_timer.setInterval(max(1, seconds) * 1000)

    def show_placeholder(self, text: str) -> None:
        self.stop()
        self._placeholder.setText(text)
        self._stack.setCurrentWidget(self._placeholder)

    def show_media(self, path: Path, media_type: str) -> None:
        self.stop()
        if media_type == MEDIA_VIDEO:
            self._showing_video = True
            self._stack.setCurrentWidget(self._video)
            self._player.setSource(QUrl.fromLocalFile(str(path)))
            self._player.play()
            return

        reader = QImageReader(str(path))
        reader.setAutoTransform(True)
        size = reader.size()
        if size.isValid() and max(size.width(), size.height()) > MAX_DECODE_SIDE:
            reader.setScaledSize(
                size.scaled(QSize(MAX_DECODE_SIDE, MAX_DECODE_SIDE), Qt.AspectRatioMode.KeepAspectRatio)
            )
        image = reader.read()
        if image.isNull():
            logger.warning("Could not decode image %s: %s", path.name, reader.errorString())
            self.failed.emit("เปิดรูปภาพนี้ไม่ได้")
            return
        self._pixmap = QPixmap.fromImage(image)
        self._stack.setCurrentWidget(self._image)
        self._rescale_image()
        self._image_timer.start()

    def pause(self) -> None:
        self._paused = True
        if self._showing_video:
            self._player.pause()
        else:
            self._image_timer.stop()

    def resume(self) -> None:
        if not self._paused:
            return
        self._paused = False
        if self._showing_video:
            self._player.play()
        elif not self._pixmap.isNull():
            self._image_timer.start()

    def is_paused(self) -> bool:
        return self._paused

    def stop(self) -> None:
        self._image_timer.stop()
        self._paused = False
        if self._showing_video:
            self._showing_video = False
            self._player.stop()
            self._player.setSource(QUrl())
        self._pixmap = QPixmap()
        self._image.clear()

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self._rescale_image()

    def _rescale_image(self) -> None:
        if self._pixmap.isNull():
            return
        ratio = self.devicePixelRatioF()
        scaled = self._pixmap.scaled(
            self.size() * ratio,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        scaled.setDevicePixelRatio(ratio)
        self._image.setPixmap(scaled)

    def _on_media_status(self, status: QMediaPlayer.MediaStatus) -> None:
        if self._showing_video and status == QMediaPlayer.MediaStatus.EndOfMedia:
            # Deferred: listeners load the next item, which must not happen
            # while the player is still delivering this status change.
            QTimer.singleShot(0, self.finished.emit)

    def _on_player_error(self, error: QMediaPlayer.Error, message: str) -> None:
        if not self._showing_video or error == QMediaPlayer.Error.NoError:
            return
        logger.warning("Video playback error: %s (%s)", message, error)
        self.failed.emit("เล่นวิดีโอนี้ไม่ได้")
