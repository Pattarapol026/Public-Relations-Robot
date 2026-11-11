"""Check the real microphone end to end, with the same code the robot uses.

    .venv313\\Scripts\\python.exe tools\\mic_check.py                 # speak during the 5 s
    .venv313\\Scripts\\python.exe tools\\mic_check.py --say "ห้องสมุดเปิดกี่โมง"
    .venv313\\Scripts\\python.exe tools\\mic_check.py --device "Microphone Array 1 () [WDM-KS]"

It lists the microphones, opens one (or the first that works), records,
prints the levels and transcribes what was heard. With ``--say`` the robot's
own voice speaks the sentence through the speakers while recording, so the
whole path (air, microphone, speech-to-text) is tested without anyone talking.
Nothing is stored; the recording stays in memory.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402

from audio.microphone import MicrophoneError, MicrophoneService, windows_microphone_blocked  # noqa: E402
from audio.speech_to_text import SpeechToTextService, SttConfig  # noqa: E402
from config.settings import load_settings  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--seconds", type=float, default=5.0, help="how long to record")
    parser.add_argument("--device", default=None, help="microphone name as listed (default: automatic)")
    parser.add_argument("--channel", type=int, default=0)
    parser.add_argument("--say", default=None, help="speak this through the speakers while recording")
    parser.add_argument("--language", default=None, help="force 'th' or 'en' (default: as configured)")
    parser.add_argument("--no-stt", action="store_true", help="only record and measure")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    settings = load_settings()
    microphone = MicrophoneService()
    print(f"Windows privacy blocks microphone access: {'yes' if windows_microphone_blocked() else 'no'}")
    try:
        devices = microphone.list_input_devices()
    except MicrophoneError as exc:
        print(f"FAIL listing microphones: {exc}")
        return 1
    print("Microphones:")
    for device in devices:
        print(f"  {'*' if device.is_default else ' '} {device.name}")

    speech = None
    if args.say:
        speech = _prepare_speech(settings, args.say)

    try:
        started = time.monotonic()
        microphone.start(args.device, args.channel, fallback=args.device is None)
        opened = time.monotonic() - started
    except MicrophoneError as exc:
        print(f"FAIL opening the microphone: {exc}")
        return 1
    print(f"Opened {microphone.device_label} in {opened:.2f} s; recording {args.seconds:.0f} s...")
    levels = []
    try:
        if speech is not None:
            playback, samples, rate = speech
            time.sleep(0.5)
            playback.feed(samples, rate)
            playback.finish()
        deadline = time.monotonic() + args.seconds
        while time.monotonic() < deadline:
            time.sleep(0.05)
            levels.append(microphone.level)
    finally:
        audio = microphone.stop()  # always closes the stream
        if speech is not None:
            speech[0].stop()
    print(f"Closed the microphone: recording={microphone.is_recording}")

    levels_array = np.array(levels) if levels else np.zeros(1)
    print(
        f"Recorded {len(audio) / 16000:.1f} s at 16 kHz | peak {float(np.abs(audio).max()) if len(audio) else 0:.4f}"
        f" | level median {np.median(levels_array):.4f}, max {levels_array.max():.4f}"
        f" | speech threshold {settings.mic_level_threshold:.3f}"
    )
    if len(audio) and float(np.abs(audio).max()) == 0.0:
        print("FAIL the microphone delivered only digital silence (muted, or access denied)")
        return 1
    if args.no_stt:
        return 0

    stt = SpeechToTextService(SttConfig(
        model=settings.stt_model, device=settings.stt_device, compute_type=settings.stt_compute_type,
        language=settings.stt_language, model_dir=settings.model_dir,
    ))
    started = time.monotonic()
    text = stt.transcribe(audio, args.language)
    print(f"Transcribed in {time.monotonic() - started:.1f} s: {text!r}")
    if args.say:
        print(f"Expected:              {args.say!r}")
    return 0 if text else 2


def _prepare_speech(settings, text: str):
    from audio.text_to_speech import TextToSpeechService, TtsConfig
    from services.audio_playback_service import AudioPlaybackService

    tts = TextToSpeechService(TtsConfig())
    samples = tts.synthesize(text)
    playback = AudioPlaybackService()
    playback.start(tts.sample_rate)
    return playback, samples, tts.sample_rate


if __name__ == "__main__":
    raise SystemExit(main())
