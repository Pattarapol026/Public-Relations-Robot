from __future__ import annotations

import dataclasses

import pytest

from database.models import MEDIA_IMAGE, MEDIA_VIDEO
from database.repositories import MOVE_UP, MediaRepository
from services.media_service import MediaError, MediaService, sniff_signature

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64
MP4 = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64
WEBM = b"\x1a\x45\xdf\xa3" + b"\x00" * 64
AVI = b"RIFF\x00\x00\x00\x00AVI LIST" + b"\x00" * 64
WEBP = b"RIFF\x00\x00\x00\x00WEBPVP8 " + b"\x00" * 64


@pytest.fixture
def media_dir(tmp_path):
    return tmp_path / "media"


@pytest.fixture
def service(database, media_dir) -> MediaService:
    return MediaService(
        MediaRepository(database), media_dir, max_video_bytes=4096, max_image_bytes=1024
    )


def make_file(tmp_path, name: str, content: bytes):
    source_dir = tmp_path / "uploads"
    source_dir.mkdir(exist_ok=True)
    path = source_dir / name
    path.write_bytes(content)
    return path


@pytest.mark.parametrize(
    ("name", "content", "media_type"),
    [
        ("intro.mp4", MP4, MEDIA_VIDEO),
        ("clip.WEBM", WEBM, MEDIA_VIDEO),
        ("old.avi", AVI, MEDIA_VIDEO),
        ("poster.png", PNG, MEDIA_IMAGE),
        ("photo.JPG", JPEG, MEDIA_IMAGE),
        ("banner.webp", WEBP, MEDIA_IMAGE),
    ],
)
def test_valid_file_is_copied_under_a_generated_name(
    service, media_dir, tmp_path, name, content, media_type
):
    source = make_file(tmp_path, name, content)
    item = service.import_file(source, "  ชื่อสื่อ  ", " คำอธิบาย ", " หมวด ")

    assert (item.title, item.description, item.category) == ("ชื่อสื่อ", "คำอธิบาย", "หมวด")
    assert (item.media_type, item.original_name, item.size_bytes) == (
        media_type, name, len(content),
    )
    assert item.enabled is True
    # The stored name is generated: 32 hex chars + lowercased extension, never the upload's name.
    stem, _, extension = item.file_name.partition(".")
    assert len(stem) == 32 and int(stem, 16) >= 0
    assert "." + extension == source.suffix.lower()
    stored = service.resolve_path(item)
    assert stored.parent == media_dir.resolve()
    assert stored.read_bytes() == content
    assert source.exists()  # the original is copied, not moved
    assert [p.name for p in media_dir.iterdir()] == [item.file_name]  # no leftover .part file


@pytest.mark.parametrize(
    ("name", "content"),
    [
        ("script.exe", b"MZ" + b"\x00" * 64),  # extension not allowed
        ("noextension", MP4),
        ("page.html", b"<html>"),
        ("fake.mp4", b"MZ this is not a video" + b"\x00" * 64),  # content does not match
        ("fake.png", JPEG),  # a real image, but not the claimed type
        ("fake.jpg", b"<?php echo 1; ?>" + b"\x00" * 64),
        ("empty.png", b""),
        ("big.png", PNG + b"\x00" * 2000),  # over the 1024-byte image limit
        ("big.mp4", MP4 + b"\x00" * 5000),  # over the 4096-byte video limit
    ],
)
def test_invalid_file_is_refused_and_nothing_is_stored(service, media_dir, tmp_path, name, content):
    source = make_file(tmp_path, name, content)
    with pytest.raises(MediaError):
        service.import_file(source, "ชื่อ")
    assert service.list() == []
    assert not media_dir.exists() or list(media_dir.iterdir()) == []


def test_missing_source_and_directory_source_are_refused(service, tmp_path):
    with pytest.raises(MediaError):
        service.import_file(tmp_path / "nope.mp4", "ชื่อ")
    directory = tmp_path / "folder.mp4"
    directory.mkdir()
    with pytest.raises(MediaError):
        service.import_file(directory, "ชื่อ")


@pytest.mark.parametrize(
    ("title", "description", "category"),
    [("", "", ""), ("   ", "", ""), ("x" * 121, "", ""), ("ok", "x" * 501, ""), ("ok", "", "x" * 61)],
)
def test_invalid_metadata_is_refused(service, tmp_path, title, description, category):
    source = make_file(tmp_path, "poster.png", PNG)
    with pytest.raises(MediaError):
        service.import_file(source, title, description, category)
    assert service.list() == []


def test_upload_name_cannot_steer_where_the_file_is_written(service, media_dir, tmp_path):
    hostile = make_file(tmp_path, "..%2f..%2fevil;rm -rf.png", PNG)
    item = service.import_file(hostile, "ชื่อ")
    assert service.resolve_path(item).parent == media_dir.resolve()
    assert "evil" not in item.file_name


@pytest.mark.parametrize(
    "file_name",
    [
        "../secret.mp4",
        "..\\secret.mp4",
        "sub/" + "a" * 32 + ".mp4",
        "C:\\Windows\\win.ini",
        "/etc/passwd",
        "a" * 32,  # no extension
        "A" * 32 + ".mp4",  # not lowercase hex
        "a" * 32 + ".mp4/..",
        "",
    ],
)
def test_resolve_path_refuses_names_that_did_not_come_from_this_service(
    service, tmp_path, file_name
):
    item = service.import_file(make_file(tmp_path, "poster.png", PNG), "ชื่อ")
    tampered = dataclasses.replace(item, file_name=file_name)
    with pytest.raises(MediaError):
        service.resolve_path(tampered)


def test_delete_removes_row_and_file(service, tmp_path):
    item = service.import_file(make_file(tmp_path, "poster.png", PNG), "ชื่อ")
    stored = service.resolve_path(item)
    service.delete(item.id)
    assert service.list() == []
    assert not stored.exists()
    service.delete(item.id)  # deleting again is harmless


def test_playlist_has_only_enabled_items_with_existing_files_in_order(service, tmp_path):
    first = service.import_file(make_file(tmp_path, "a.png", PNG), "หนึ่ง")
    second = service.import_file(make_file(tmp_path, "b.mp4", MP4), "สอง")
    third = service.import_file(make_file(tmp_path, "c.jpg", JPEG), "สาม")
    fourth = service.import_file(make_file(tmp_path, "d.png", PNG), "สี่")

    service.update(second.id, "สอง", "", "", enabled=False)
    service.resolve_path(third).unlink()  # file removed behind the app's back
    service.move(fourth.id, MOVE_UP)
    service.move(fourth.id, MOVE_UP)
    service.move(fourth.id, MOVE_UP)

    assert [item.title for item, _path in service.playlist()] == ["สี่", "หนึ่ง"]
    assert [item.title for item in service.list()] == ["สี่", "หนึ่ง", "สอง", "สาม"]


def test_update_validates_metadata(service, tmp_path):
    item = service.import_file(make_file(tmp_path, "poster.png", PNG), "ชื่อ")
    with pytest.raises(MediaError):
        service.update(item.id, "", "", "", True)
    service.update(item.id, " ชื่อใหม่ ", "", "ข่าว", True)
    assert service.list()[0].title == "ชื่อใหม่"


def test_sniff_signature_needs_real_container_bytes():
    assert sniff_signature(MP4) == "iso"
    assert sniff_signature(b"\x00\x00\x00\x08wide\x00\x00\x00\x00mdat") == "iso"
    assert sniff_signature(b"GIF89a" + b"\x00" * 10) == "gif"
    assert sniff_signature(b"BM" + b"\x00" * 14) == "bmp"
    assert sniff_signature(b"RIFF\x00\x00\x00\x00WAVEfmt ") is None
    assert sniff_signature(b"") is None
