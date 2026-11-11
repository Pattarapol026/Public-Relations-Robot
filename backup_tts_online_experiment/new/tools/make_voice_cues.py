"""Makes the small sounds the robot uses to sound like a person: a chuckle, a laugh, a sigh, "hmm", "oh!".

Run once (about 3 minutes; needs the internet): ``python -m tools.make_voice_cues``. The files go to
``media/voice_cues/`` and are used from there, with no network, from then on. Delete a file and run it
again to make a new take of it; files that exist are left alone.

* Laughs and sighs are not words, so a speech voice cannot say them. They come from Gemini's speech model
  (``gemini-3.1-flash-tts-preview``: it follows directions like ``[chuckles softly]``; the 3.8 models read
  the directions aloud). The free plan allows 3 requests a minute, so this waits between requests.
* "Hmm", "oh!", "wow" are short words: they are made by the same online voices as the answers (Niwat for Thai,
  Andrew for English), so they sound like the same speaker.

Every clip is checked: a clip longer than 3 s is thrown away (the model started to speak), and all are
levelled and faded like the rest of the speech (audio/voice_polish.py).
"""
from __future__ import annotations

import sys
import time
import wave
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from audio import voice_polish  # noqa: E402
from audio.edge_voice import EdgeVoice  # noqa: E402

OUT = ROOT / "media" / "voice_cues"
GEMINI_MODEL = "gemini-3.1-flash-tts-preview"
GEMINI_VOICE = "Achernar"  # a soft woman's voice, to go with the speech voice (the first set, made with a man's voice, was set aside)
REQUEST_EVERY_S = 22.0  # 3 a minute on the free plan
MAX_CLIP_S = 3.0
RATE = 24_000

# name -> directions for Gemini. Several takes of a sound, so the robot does not laugh the same way twice.
GEMINI_CLIPS = {
    "chuckle_1": "[soft chuckle] heh heh",
    "chuckle_2": "Make only a short, gentle, friendly chuckle, with no words at all: [chuckles]",
    "chuckle_3": "[chuckles softly] hehe",
    "laugh_1": "[laughs softly]",
    "laugh_2": "[laughs warmly] haha",
    "sigh_1": "[sighs softly]",
}
# name -> (voice, text, rate in %) from the online speech voices; "hmm" is drawn out, as a person does while thinking
EDGE_CLIPS = {
    "hmm_th": ("th-TH-PremwadeeNeural", "อืมม", -35),
    "aha_th": ("th-TH-PremwadeeNeural", "อ๋อ", -10),
    "wow_th": ("th-TH-PremwadeeNeural", "โอ้โห", -10),
    "hmm_en": ("en-US-AvaNeural", "Hmmmm...", -40),
    "aha_en": ("en-US-AvaNeural", "Oh, I see.", -5),
    "wow_en": ("en-US-AvaNeural", "Wow!", -5),
}


def save(name: str, audio: np.ndarray) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    pcm = np.clip(audio * 32767.0, -32768, 32767).astype(np.int16)
    with wave.open(str(OUT / f"{name}.wav"), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(RATE)
        handle.writeframes(pcm.tobytes())
    print(f"  {name}: {len(audio) / RATE:.1f} s", flush=True)


def trim(audio: np.ndarray) -> np.ndarray:
    loud = np.flatnonzero(np.abs(audio) > 0.01)
    if len(loud) == 0:
        return audio
    edge = int(0.04 * RATE)
    return audio[max(0, loud[0] - edge):loud[-1] + 1 + edge]


def make_edge_clips() -> None:
    voice = EdgeVoice()
    for name, (speaker, text, rate) in EDGE_CLIPS.items():
        if (OUT / f"{name}.wav").exists():
            continue
        audio = None
        for _attempt in range(5):  # Premwadee fails about a third of the time
            try:
                audio = voice.synthesize(text, speaker, rate_percent=rate, pitch_hz=0, starts=(0.0, 0.0, 1.2))
                break
            except Exception as exc:  # noqa: BLE001
                print(f"  {name}: attempt failed ({exc})")
        if audio is None:
            continue
        save(name, voice_polish.polish(trim(audio)))


def make_gemini_clips() -> None:
    from config.settings import load_settings
    from google import genai
    from google.genai import types

    key = load_settings().gemini_api_key
    if not key:
        print("No Gemini key: the laughs are not made.")
        return
    client = genai.Client(api_key=key)
    last = 0.0
    for name, directions in GEMINI_CLIPS.items():
        if (OUT / f"{name}.wav").exists():
            continue
        for attempt in range(3):
            wait = REQUEST_EVERY_S - (time.monotonic() - last)
            if wait > 0:
                time.sleep(wait)
            last = time.monotonic()
            try:
                response = client.models.generate_content(
                    model=GEMINI_MODEL, contents=directions,
                    config=types.GenerateContentConfig(
                        response_modalities=["AUDIO"],
                        speech_config=types.SpeechConfig(voice_config=types.VoiceConfig(
                            prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=GEMINI_VOICE))),
                    ),
                )
                data = response.candidates[0].content.parts[0].inline_data.data
            except Exception as exc:  # noqa: BLE001
                message = str(exc)
                if "per day" in message.lower() or "PerDay" in message:
                    print(f"  {name}: the daily limit is reached; run this again tomorrow")
                    return
                print(f"  {name}: attempt {attempt + 1} failed ({message[:80]})")
                continue
            if data[:4] == b"RIFF":
                data = data[44:]
            audio = np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768.0
            audio = trim(audio)
            if not 0.3 <= len(audio) / RATE <= MAX_CLIP_S:
                print(f"  {name}: {len(audio) / RATE:.1f} s is not a short sound; trying again")
                continue
            save(name, voice_polish.polish(audio))
            break


if __name__ == "__main__":
    print("Words (hmm, oh, wow) from the online voices:")
    make_edge_clips()
    print("Laughs and sighs from Gemini (about 22 s each):")
    make_gemini_clips()
    print("Done:", ", ".join(sorted(p.stem for p in OUT.glob("*.wav"))))
