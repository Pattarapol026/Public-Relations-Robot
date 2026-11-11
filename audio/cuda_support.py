"""Lets CTranslate2 (faster-whisper) find the NVIDIA runtime libraries that pip installs.

The speech models run about ten times faster on the laptop's NVIDIA graphics card than on its
processor (measured 7 Nov 2026 on an RTX 3050 Ti: the Thai medium model 0.5 s against 5.4 s). The
card needs cuBLAS and cuDNN, which come as the pip packages ``nvidia-cublas-cu12`` and
``nvidia-cudnn-cu12``. They put their DLLs in folders Windows does not search, so the folders are
added here, once, before ctranslate2 is first imported (see ``main.py``).

Nothing here may raise: without the packages, or without a card, the speech service falls back to the
processor on its own.
"""
from __future__ import annotations

import importlib.util
import logging
import os
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

_registered: list[str] | None = None


def register_cuda_libraries() -> list[str]:
    """Add the pip-installed NVIDIA DLL folders to the DLL search path. Returns the folders added."""
    global _registered
    if _registered is not None:
        return _registered
    _registered = []
    if sys.platform != "win32":
        return _registered  # Linux finds them through the loader or LD_LIBRARY_PATH
    try:
        spec = importlib.util.find_spec("nvidia")
        roots = [Path(p) for p in (spec.submodule_search_locations or [])] if spec else []
        for root in roots:
            for bin_dir in sorted(root.glob("*/bin")):
                folder = str(bin_dir)
                os.add_dll_directory(folder)
                os.environ["PATH"] = folder + os.pathsep + os.environ.get("PATH", "")
                _registered.append(folder)
    except Exception:  # noqa: BLE001 - optional acceleration; never stop the program
        logger.warning("Could not register the NVIDIA libraries", exc_info=True)
    if _registered:
        logger.info("NVIDIA libraries for speech on the graphics card: %d folders", len(_registered))
    return _registered


def cuda_available() -> bool:
    """True when ctranslate2 can see an NVIDIA card (call ``register_cuda_libraries`` first)."""
    try:
        import ctranslate2

        return ctranslate2.get_cuda_device_count() > 0
    except Exception:  # noqa: BLE001
        return False
