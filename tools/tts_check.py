"""Check speaking end to end on the real speaker, with the same code the robot uses.

    .venv313\\Scripts\\python.exe tools\\tts_check.py                # all cases, at 0.90
    .venv313\\Scripts\\python.exe tools\\tts_check.py --speed 1.0
    .venv313\\Scripts\\python.exe tools\\tts_check.py --hear         # also let Whisper transcribe each case
    .venv313\\Scripts\\python.exe tools\\tts_check.py --only 1,3

Each case goes through the real SpeechController, voices and audio stream. It prints
which voice said each piece and how long it lasted, and checks that the "finished"
signal came only after the last sound reached the speaker (from PortAudio's own clock).
Whether it sounds right can only be judged by a person: turn the speakers up and listen.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402
from PySide6.QtCore import QCoreApplication, QThreadPool  # noqa: E402

from app.speech_controller import SpeechController, SpeechStatus  # noqa: E402
from audio.text_to_speech import TextToSpeechService, TtsConfig, speech_pieces  # noqa: E402
from services.audio_playback_service import AudioPlaybackService  # noqa: E402

CASES = [
    ("ลงท้ายครับ", "ยินดีต้อนรับสู่วิทยาลัยครับ"),
    ("ลงท้ายค่ะ", "ขอบคุณที่มาเยี่ยมชมค่ะ"),
    ("ไทยหลายประโยค", "ฝ่ายทะเบียนเปิดทำการวันจันทร์ถึงวันศุกร์ครับ. นักศึกษาใหม่ต้องเตรียมเอกสารมาให้ครบ. "
                     "หากมีข้อสงสัยสอบถามเจ้าหน้าที่ได้เลยครับ"),
    ("อังกฤษล้วน", "Welcome to the college. How may I help you today?"),
    ("อังกฤษคำยาว+วรรคตอน", "Registration, tuition payments, and scholarship applications are handled "
                           "at the administration building; please bring your identification!"),
    ("สลับ: ไทย", "สวัสดีครับ มีอะไรให้ช่วยไหมครับ"),
    ("สลับ: อังกฤษ", "Hello, the library is on the second floor."),
    ("สลับ: ไทย", "ห้องสมุดอยู่ชั้นสองครับ"),
    ("สลับ: อังกฤษ", "Thank you for visiting."),
    ("ข้อความยาวหลายชิ้น", " ".join(["วิทยาลัยของเราเปิดสอนหลายแผนกวิชาตั้งแต่ระดับประกาศนียบัตรวิชาชีพถึงปริญญาตรี"] * 4)
                         + " ติดต่อสอบถามเพิ่มเติมได้ที่ฝ่ายประชาสัมพันธ์ครับ"),
    ("เบอร์โทรยาวเกินโมเดล", "โทร 02-123-4567 " * 9 + "ครับ"),
    ("อังกฤษมีชื่อไทย", "Please contact ฝ่ายทะเบียน at the main building for more details."),
]


class ClockedPlayback(AudioPlaybackService):
    """Remembers when (on PortAudio's clock) the last real sample reaches the speaker."""

    def __init__(self) -> None:
        super().__init__()
        self.last_sound_at = 0.0  # DAC time
        self.clock = lambda: 0.0
        self.empty_blocks = 0

    def start(self, sample_rate: int, device_name: str | None = None) -> None:
        self.last_sound_at = 0.0
        self.empty_blocks = 0
        super().start(sample_rate, device_name)
        stream = self._stream
        self.clock = lambda: stream.time

    def _on_audio(self, outdata, frames, time_info, status) -> None:
        was_finishing = self._finishing
        super()._on_audio(outdata, frames, time_info, status)
        sound = np.flatnonzero(outdata[:, 0])
        if len(sound):
            rate = self._stream_rate or 24_000.0
            self.last_sound_at = float(time_info.outputBufferDacTime) + (int(sound[-1]) + 1) / rate
        elif not was_finishing:
            self.empty_blocks += 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Speak the test sentences on the real speaker")
    parser.add_argument("--speed", type=float, default=0.9)
    parser.add_argument("--hear", action="store_true", help="also transcribe the audio with Whisper")
    parser.add_argument("--only", default="", help="case numbers, such as 1,3")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

    app = QCoreApplication([])
    tts = TextToSpeechService(TtsConfig(speed=args.speed))
    playback = ClockedPlayback()
    speech = SpeechController(True, tts, playback, lambda: None, QThreadPool.globalInstance())
    while speech.status is SpeechStatus.LOADING:
        app.processEvents()
        time.sleep(0.02)
    print(f"speech: {speech.status_text}; speed {args.speed}")
    if not speech.is_ready:
        return 1

    whisper = None
    if args.hear:
        from faster_whisper import WhisperModel

        whisper = WhisperModel("small", device="cpu", compute_type="int8")

    outcome: list[str] = []
    speech.finished.connect(lambda: outcome.append("finished"))
    speech.failed.connect(lambda error: outcome.append(f"FAILED: {error}"))
    wanted = {int(n) for n in args.only.split(",") if n}
    problems = 0
    for number, (name, text) in enumerate(CASES, 1):
        if wanted and number not in wanted:
            continue
        pieces = speech_pieces(text)
        voices = "+".join(language for _text, language in pieces)
        print(f"\n[{number}] {name}: {len(pieces)} pieces, voices {voices}")
        print(f"    {text[:100]}")
        outcome.clear()
        started = time.monotonic()
        if not speech.speak(text):
            print(f"    FAILED to start: {speech.status_text}")
            problems += 1
            continue
        first_sound = None
        finished_at = None
        while not outcome:
            app.processEvents()
            if first_sound is None and playback.last_sound_at:
                first_sound = time.monotonic() - started
            if playback.is_active:
                finished_at = playback.clock()
            time.sleep(0.005)
        audio = [tts.synthesize(piece, args.speed, language) for piece, language in pieces] if outcome[0] == "finished" else []
        seconds = sum(len(part) for part in audio) / tts.sample_rate
        margin_ms = (finished_at - playback.last_sound_at) * 1000 if finished_at and playback.last_sound_at else None
        ok = outcome[0] == "finished" and margin_ms is not None and margin_ms >= 0
        problems += not ok
        print(f"    {'OK ' if ok else 'BAD'} {outcome[0]}: {seconds:.2f} s of speech, whole turn "
              f"{time.monotonic() - started:.2f} s, first sound after {first_sound or 0:.2f} s, "
              f"silent blocks waiting for synthesis {playback.empty_blocks}")
        if margin_ms is not None:
            print(f"    the last sound reached the speaker {margin_ms:.0f} ms before the stream was closed")
        if whisper is not None and audio:
            padded = np.concatenate((np.zeros(4800, np.float32), *audio, np.zeros(9600, np.float32)))
            language = "en" if pieces[0][1] == "en" else "th"
            segments, _info = whisper.transcribe(padded, language=language, beam_size=5, vad_filter=False)
            print(f"    Whisper heard: {''.join(s.text for s in segments).strip()}")
    print(f"\n{'ALL OK' if not problems else f'{problems} PROBLEM(S)'} (timing and completeness only; listening is up to a person)")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
