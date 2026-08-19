# Do This First

Steps required after a fresh clone + `docker compose build && docker compose up -d`,
before the satellite is fully functional. None of this is automatic.

## 1. Expose Piper's Wyoming port

The filler-generation script talks to Piper directly, which requires the
add-on's port 10200 to be exposed externally (it isn't by default).

In HA: Settings > Add-ons > Piper > Configuration > "Show disabled ports" >
set the host port next to `10200/tcp` to `10200` > Save > restart the add-on.

Confirm from this satellite Pi:
    nc -zv homeassistant 10200

## 2. Generate filler/wait audio files

The `sounds_custom` Docker volume starts empty. These files are NOT in git
(they live in the volume, not the repo) and must be generated after every
fresh setup, and again any time you change Piper's configured voice:

    docker exec -it linux-voice-assistant python3 /app/generate_fillers.py

Uses whichever voice is set in Settings > Voice assistants > [pipeline] >
Text-to-speech -- update VOICE_NAME in tools/generate_fillers.py to match
if you change voices, then re-run the command above.

## 3. Generate the silence pad

Also lives only in the volume, not git. Prevents the last syllable of TTS
responses from being clipped by playback ending too abruptly:

    docker exec -it linux-voice-assistant python3 -c "
    import wave
    with wave.open('/app/sounds/custom/silence_400ms.wav', 'wb') as f:
        f.setnchannels(1); f.setsampwidth(2); f.setframerate(22050)
        f.writeframes(b'\x00\x00' * int(22050 * 0.4))
    "

## 4. Verify

    docker exec -it linux-voice-assistant ls -la /app/sounds/custom/

Should show: filler_1.wav, filler_2.wav, filler_3.wav, wait_1.wav,
wait_2.wav, silence_400ms.wav

## Known issues (not fixed here, tracked upstream)

- `stop_word_sensitivity` does not survive a container restart -- resets
  to 0.5 even though it's correctly saved in preferences.json. Wake word
  sensitivities restore correctly; this appears specific to stop-word.
  (GitHub issue not yet filed against OHF-Voice/linux-voice-assistant.)
