#!/usr/bin/env python3
"""Generate filler/thinking-sound phrases via Piper's Wyoming socket.

VOICE_NAME must match whatever voice is configured on the Assist
pipeline (Settings > Voice assistants > [pipeline] > Text-to-speech) --
NOT necessarily Piper add-on's own baseline default, which can differ.

Re-run this any time you change the pipeline's TTS voice, updating
VOICE_NAME to match.
"""
import asyncio
import wave
from pathlib import Path

from wyoming.client import AsyncTcpClient
from wyoming.tts import Synthesize, SynthesizeVoice
from wyoming.audio import AudioStart, AudioChunk, AudioStop

PIPER_HOST = "homeassistant"
PIPER_PORT = 10200

# Update this to match Settings > Voice assistants > [pipeline] > Text-to-speech
VOICE_NAME = "en_US-hfc_female-medium"

OUT_DIR = Path("/app/sounds/custom")

PHRASES = {
    "filler_1.wav": "Still thinking.",
    "filler_2.wav": "One moment.",
    "filler_3.wav": "Working on it.",
    "wait_1.wav": "Okay, got it. One moment.",
    "wait_2.wav": "Almost ready.",
}

async def synth_one(text: str, out_path: Path) -> None:
    async with AsyncTcpClient(PIPER_HOST, PIPER_PORT) as client:
        await client.write_event(
            Synthesize(text=text, voice=SynthesizeVoice(name=VOICE_NAME)).event()
        )
        wav_file = None
        while True:
            event = await client.read_event()
            if event is None:
                break
            if AudioStart.is_type(event.type):
                start = AudioStart.from_event(event)
                wav_file = wave.open(str(out_path), "wb")
                wav_file.setnchannels(start.channels)
                wav_file.setsampwidth(start.width)
                wav_file.setframerate(start.rate)
            elif AudioChunk.is_type(event.type) and wav_file:
                chunk = AudioChunk.from_event(event)
                wav_file.writeframes(chunk.audio)
            elif AudioStop.is_type(event.type):
                break
        if wav_file:
            wav_file.close()


async def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for filename, text in PHRASES.items():
        out_path = OUT_DIR / filename
        print(f"Generating {filename}: {text!r} (voice={VOICE_NAME})")
        await synth_one(text, out_path)
    print("Done. Update VOICE_NAME above and re-run if you change the pipeline's voice.")


if __name__ == "__main__":
    asyncio.run(main())
