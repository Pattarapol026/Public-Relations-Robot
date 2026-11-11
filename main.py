"""Entry point: python main.py"""
from __future__ import annotations

import logging
import os
import sys

# Memory, before any numerical library loads (measured on this 7.7 GB laptop, 2026-11-05):
# CTranslate2's MKL backend committed 0.7-4.6 GB per speech model on top of the model itself
# (9 GB for the app, more than the RAM), so Windows paged the models out while the robot
# waited, and the next question was slow and speech stuttered. Without MKL the three models
# commit 1 GB in all, transcribe the same text, at most ~10 % slower when nothing else runs.
# OpenBLAS (numpy) reserves a buffer per thread: 493 MB for 16 threads, 108 MB for 4.
os.environ.setdefault("CT2_USE_MKL", "0")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "4")

# The speech models run on the NVIDIA card when there is one (ten times faster than the processor): its
# libraries must be on the DLL search path before ctranslate2 is first imported. Never raises.
from audio.cuda_support import register_cuda_libraries  # noqa: E402

register_cuda_libraries()

from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402 (after the settings above)

from app.application import Application
from app.logging_setup import setup_logging
from config.settings import DEFAULT_ENV_PATH, ConfigError, load_settings
from ui.fonts import apply_app_font
from ui.log_bridge import QtLogHandler


def main() -> int:
    qt_app = QApplication(sys.argv)
    try:
        settings = load_settings()
    except ConfigError as exc:
        QMessageBox.critical(None, "ตั้งค่าไม่ถูกต้อง", str(exc))
        return 2

    log_handler = QtLogHandler()
    setup_logging(
        settings.log_dir,
        settings.log_level,
        secrets=[settings.gemini_api_key, settings.mqtt_password],
        extra_handlers=[log_handler],
    )

    apply_app_font(qt_app)

    # pythainlp is imported later by two threads at once (the speech worker loading PyThaiTTS and
    # the admin screen wrapping Thai text); that deadlocked on 2026-11-07 and left the robot
    # without a voice. Importing it here, before any worker starts, rules that out.
    try:
        import pythainlp.tokenize  # noqa: F401
        import pythainlp.util  # noqa: F401
    except Exception:  # noqa: BLE001 - optional; the code that needs it handles its absence
        logging.getLogger(__name__).warning("pythainlp preload failed", exc_info=True)

    application = Application(settings, log_handler, env_path=DEFAULT_ENV_PATH)
    qt_app.aboutToQuit.connect(application.shutdown)
    application.show()
    exit_code = qt_app.exec()
    if not application.finished_cleanly:
        # A background task that cannot be interrupted (typically the first
        # speech-model download) is still running; do not keep the process
        # alive waiting for it after the window has closed.
        logging.shutdown()
        os._exit(exit_code)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
