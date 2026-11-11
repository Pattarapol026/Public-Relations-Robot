"""Stores uploaded media files safely and keeps their metadata in the database.

Files live in the media directory under names this service generates; the
database only holds metadata and that generated name. Nothing here trusts the
uploaded file's own name, extension claim, or any name read back from the DB.
"""
from __future__ import annotations

import logging
import os
import re
import shutil
import uuid
from pathlib import Path

from database.models import MEDIA_IMAGE, MEDIA_VIDEO, MediaItem
from database.repositories import MediaRepository

logger = logging.getLogger(__name__)

MAX_TITLE_CHARS = 120
MAX_DESCRIPTION_CHARS = 500
MAX_CATEGORY_CHARS = 60

# extension -> (media type, accepted container signatures)
_FORMATS: dict[str, tuple[str, frozenset[str]]] = {
    ".mp4": (MEDIA_VIDEO, frozenset({"iso"})),
    ".m4v": (MEDIA_VIDEO, frozenset({"iso"})),
    ".mov": (MEDIA_VIDEO, frozenset({"iso"})),
    ".webm": (MEDIA_VIDEO, frozenset({"matroska"})),
    ".mkv": (MEDIA_VIDEO, frozenset({"matroska"})),
    ".avi": (MEDIA_VIDEO, frozenset({"avi"})),
    ".png": (MEDIA_IMAGE, frozenset({"png"})),
    ".jpg": (MEDIA_IMAGE, frozenset({"jpeg"})),
    ".jpeg": (MEDIA_IMAGE, frozenset({"jpeg"})),
    ".gif": (MEDIA_IMAGE, frozenset({"gif"})),
    ".bmp": (MEDIA_IMAGE, frozenset({"bmp"})),
    ".webp": (MEDIA_IMAGE, frozenset({"webp"})),
}
VIDEO_EXTENSIONS = tuple(ext for ext, (kind, _) in _FORMATS.items() if kind == MEDIA_VIDEO)
IMAGE_EXTENSIONS = tuple(ext for ext, (kind, _) in _FORMATS.items() if kind == MEDIA_IMAGE)

_STORED_NAME = re.compile(r"[0-9a-f]{32}\.[a-z0-9]{2,5}")
_ISO_BOX_TYPES = (b"ftyp", b"moov", b"mdat", b"free", b"wide", b"skip")


class MediaError(Exception):
    """A media operation was refused; the message is safe to show to the Admin."""


def sniff_signature(header: bytes) -> str | None:
    """Identify a container from its first bytes (needs at least 12)."""
    if header.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if header.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if header.startswith((b"GIF87a", b"GIF89a")):
        return "gif"
    if header.startswith(b"BM"):
        return "bmp"
    if header.startswith(b"RIFF") and header[8:12] == b"WEBP":
        return "webp"
    if header.startswith(b"RIFF") and header[8:12] == b"AVI ":
        return "avi"
    if header.startswith(b"\x1a\x45\xdf\xa3"):
        return "matroska"
    if header[4:8] in _ISO_BOX_TYPES:
        return "iso"
    return None


class MediaService:
    def __init__(
        self,
        repository: MediaRepository,
        media_dir: Path,
        max_video_bytes: int,
        max_image_bytes: int,
    ) -> None:
        self._repository = repository
        self._media_dir = Path(media_dir)
        self._limits = {MEDIA_VIDEO: max_video_bytes, MEDIA_IMAGE: max_image_bytes}

    def import_file(
        self, source: Path, title: str, description: str = "", category: str = ""
    ) -> MediaItem:
        """Validate ``source`` and copy it into the media directory. Blocking."""
        source = Path(source)
        title, description, category = _clean_metadata(title, description, category)

        extension = source.suffix.lower()
        if extension not in _FORMATS:
            allowed = ", ".join(sorted(_FORMATS))
            raise MediaError(f"ไม่รองรับไฟล์ชนิด '{extension or 'ไม่มีนามสกุล'}' (รองรับ: {allowed})")
        media_type, signatures = _FORMATS[extension]

        try:
            if not source.is_file():
                raise MediaError("ไม่พบไฟล์ที่เลือก")
            size = source.stat().st_size
            with source.open("rb") as handle:
                header = handle.read(16)
        except OSError as exc:
            raise MediaError("อ่านไฟล์ที่เลือกไม่ได้") from exc

        if size == 0:
            raise MediaError("ไฟล์ว่างเปล่า")
        limit = self._limits[media_type]
        if size > limit:
            raise MediaError(
                f"ไฟล์ใหญ่เกินกำหนด ({size / 1_048_576:.1f} MB) "
                f"ขนาดสูงสุดคือ {limit / 1_048_576:.0f} MB"
            )
        if sniff_signature(header) not in signatures:
            raise MediaError(f"เนื้อหาไฟล์ไม่ตรงกับนามสกุล {extension} หรือไฟล์เสียหาย")

        self._media_dir.mkdir(parents=True, exist_ok=True)
        stored_name = f"{uuid.uuid4().hex}{extension}"
        destination = self._media_dir / stored_name
        partial = self._media_dir / f"{stored_name}.part"
        try:
            shutil.copyfile(source, partial)
            os.replace(partial, destination)
        except OSError as exc:
            partial.unlink(missing_ok=True)
            raise MediaError("บันทึกไฟล์ลงโฟลเดอร์สื่อไม่สำเร็จ") from exc

        try:
            item = self._repository.add(
                title, description, category, media_type, stored_name, source.name[:255], size
            )
        except Exception:
            destination.unlink(missing_ok=True)
            raise
        logger.info("Media imported: id=%s type=%s file=%s", item.id, media_type, stored_name)
        return item

    def list(self, enabled_only: bool = False) -> list[MediaItem]:
        return self._repository.list(enabled_only)

    def update(
        self, item_id: int, title: str, description: str, category: str, enabled: bool
    ) -> None:
        title, description, category = _clean_metadata(title, description, category)
        self._repository.update(item_id, title, description, category, enabled)

    def move(self, item_id: int, direction: int) -> bool:
        return self._repository.move(item_id, direction)

    def delete(self, item_id: int) -> None:
        item = self._repository.get(item_id)
        if item is None:
            return
        self._repository.delete(item_id)
        try:
            self.resolve_path(item).unlink(missing_ok=True)
        except (MediaError, OSError):
            logger.exception("Could not remove media file for id=%s", item_id)
        logger.info("Media deleted: id=%s", item_id)

    def resolve_path(self, item: MediaItem) -> Path:
        """Return the file path for ``item``, refusing anything outside the media dir."""
        if not _STORED_NAME.fullmatch(item.file_name):
            raise MediaError("ชื่อไฟล์สื่อในฐานข้อมูลไม่ถูกต้อง")
        root = self._media_dir.resolve()
        path = (root / item.file_name).resolve()
        if path.parent != root:
            raise MediaError("ตำแหน่งไฟล์สื่ออยู่นอกโฟลเดอร์ที่อนุญาต")
        return path

    def playlist(self) -> list[tuple[MediaItem, Path]]:
        """Enabled items, in display order, whose files actually exist."""
        result = []
        for item in self._repository.list(enabled_only=True):
            try:
                path = self.resolve_path(item)
            except MediaError:
                logger.warning("Skipping media id=%s: invalid stored file name", item.id)
                continue
            if path.is_file():
                result.append((item, path))
            else:
                logger.warning("Skipping media id=%s: file is missing", item.id)
        return result


def _clean_metadata(title: str, description: str, category: str) -> tuple[str, str, str]:
    title, description, category = title.strip(), description.strip(), category.strip()
    if not title:
        raise MediaError("กรุณากรอกชื่อสื่อ")
    if len(title) > MAX_TITLE_CHARS:
        raise MediaError(f"ชื่อสื่อยาวเกิน {MAX_TITLE_CHARS} ตัวอักษร")
    if len(description) > MAX_DESCRIPTION_CHARS:
        raise MediaError(f"คำอธิบายยาวเกิน {MAX_DESCRIPTION_CHARS} ตัวอักษร")
    if len(category) > MAX_CATEGORY_CHARS:
        raise MediaError(f"หมวดหมู่ยาวเกิน {MAX_CATEGORY_CHARS} ตัวอักษร")
    return title, description, category
