"""Application font: the Sarabun typeface bundled in ``ui/assets/fonts``.

The app does not use an installed system font for Thai. On Windows with Qt
6.12, once a window is on screen Qt's font database can re-register the system
Thai font (Leelawadee UI, regular weight) as a fixed-size font holding only
the sizes used so far; text at any size first needed after that — a chat
message, a late-created label — is then drawn from the wrong size and comes
out garbled. Fonts registered from a file by the app are not affected, and
bundling one also gives the same look on Windows and Ubuntu.

For the same reason the stylesheet sets sizes and weights only, never a family.
"""
from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtWidgets import QApplication

logger = logging.getLogger(__name__)

FONT_DIR = Path(__file__).resolve().parent / "assets" / "fonts"
BUNDLED_FILES = ("Sarabun-Regular.ttf", "Sarabun-Bold.ttf")
BUNDLED_FAMILY = "Sarabun"

# Only used if the bundled files are missing or unreadable.
SYSTEM_FALLBACK_FAMILIES = ("Leelawadee UI", "Noto Sans Thai", "Loma", "Garuda", "Tahoma")


def apply_app_font(qt_app: QApplication) -> str | None:
    """Register the bundled font and make it the app font; returns the family used."""
    registered: set[str] = set()
    for file_name in BUNDLED_FILES:
        font_id = QFontDatabase.addApplicationFont(str(FONT_DIR / file_name))
        if font_id < 0:
            logger.error("Could not load bundled font %s", FONT_DIR / file_name)
        else:
            registered.update(QFontDatabase.applicationFontFamilies(font_id))

    if BUNDLED_FAMILY in registered:
        qt_app.setFont(QFont(BUNDLED_FAMILY))
        return BUNDLED_FAMILY

    installed = set(QFontDatabase.families())
    for family in SYSTEM_FALLBACK_FAMILIES:
        if family in installed:
            logger.warning("Bundled font unavailable; using system font %s", family)
            qt_app.setFont(QFont(family))
            return family
    logger.error("No Thai-capable font found; Thai text may not render correctly")
    return None
