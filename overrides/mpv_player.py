import time
import logging
import os
import random
import subprocess
import tempfile
import threading
from typing import Callable, Dict, List, Optional, Union
from urllib.request import urlretrieve

log = logging.getLogger("MpvMediaPlayer")

# Set to False (or comment out the elapsed-time log lines below) once
# you're done tuning filler/download timing. Doesn't affect behavior,
# only adds "(+N.Ns)"-style timing context to log lines.
DEBUG_TIMING = True

FILLER_DIR = "/app/sounds/custom"

PROCESSING_SOUND_BASENAME = "processing.wav"

FILLER_STAGES: Dict[str, dict] = {
    "thinking": {
        "files": ["filler_1.wav", "filler_2.wav", "filler_3.wav"],
        "initial_delay": 4.0,
        "interval": 6.0,
        "max_runtime": 60.0,
    },
    "fetching": {
        "files": ["wait_1.wav", "wait_2.wav"],
        "initial_delay": 3.0,
        "interval": 8.0,
        "max_runtime": 120.0, #was 15.0
    },
}


class MpvMediaPlayer:
    """Drop-in replacement that uses paplay instead of libmpv.

    Adds three things beyond stock behavior:
      1. mp3 responses are decoded via mpg123 before being handed to
         paplay, since paplay cannot reliably play some Piper/Wyoming
         mp3 output through to completion (silently truncates, even
         though the file is complete on disk).
      2. Background "filler" stages play short reassurance phrases
         during known waiting periods (LLM generation, then again
         during the TTS file download), each with its own phrasing
         and timing, stopping the instant real audio is ready.
      3. Optional elapsed-time logging (DEBUG_TIMING) for tuning the
         above without needing timestamps in the base log format.
    """

    def __init__(self, device: str | None = None) -> None:
        self._device = device
        self._done_callback: Optional[Callable[[], None]] = None

        self._stage_state = {
            name: {
                "stop": threading.Event(),
                "thread": None,
                "proc": None,
                "last": None,
                "lock": threading.Lock(),
            }
            for name in FILLER_STAGES
        }

        # Anchors for elapsed-time logging. Reset at the start of each
        # turn / download; harmless if DEBUG_TIMING is off.
        self._turn_start: Optional[float] = None
        self._download_start: Optional[float] = None

        log.info("MpvMediaPlayer (paplay backend) initialized (device=%s)", device)

    def _elapsed(self, anchor: Optional[float]) -> str:
        """Return ' (+N.Ns)' since anchor, or '' if timing is off/unset."""
        if not DEBUG_TIMING or anchor is None:
            return ""
        return f" (+{time.monotonic() - anchor:.1f}s)"

    # ------------------------------------------------------------------
    # Filler stages
    # ------------------------------------------------------------------

    def _start_stage(self, name: str) -> None:
        state = self._stage_state[name]
        with state["lock"]:
            thread = state["thread"]
            if thread is not None and thread.is_alive():
                return
            state["stop"].clear()
            state["thread"] = threading.Thread(
                target=self._stage_loop, args=(name,), daemon=True
            )
            state["thread"].start()
            anchor = self._turn_start if name == "thinking" else self._download_start
            log.debug("Filler stage '%s' started%s", name, self._elapsed(anchor))

    def _stop_stage(self, name: str) -> None:
        state = self._stage_state[name]
        with state["lock"]:
            state["stop"].set()
            proc = state["proc"]
            if proc is not None and proc.poll() is None:
                try:
                    proc.terminate()
                except Exception:
                    pass
        thread = state["thread"]
        if thread is not None and thread.is_alive():
            thread.join(timeout=2.0)
        anchor = self._turn_start if name == "thinking" else self._download_start
        log.debug("Filler stage '%s' stopped%s", name, self._elapsed(anchor))

    def _stop_all_stages(self) -> None:
        for name in FILLER_STAGES:
            self._stop_stage(name)

    def _stage_loop(self, name: str) -> None:
        cfg = FILLER_STAGES[name]
        state = self._stage_state[name]
        deadline = time.monotonic() + cfg["max_runtime"]

        if state["stop"].wait(cfg["initial_delay"]):
            return

        available = [
            os.path.join(FILLER_DIR, f)
            for f in cfg["files"]
            if os.path.exists(os.path.join(FILLER_DIR, f))
        ]
        if not available:
            log.warning("No filler files found for stage '%s' in %s", name, FILLER_DIR)
            return

        anchor = self._turn_start if name == "thinking" else self._download_start

        while not state["stop"].is_set() and time.monotonic() < deadline:
            choices = [f for f in available if f != state["last"]] or available
            choice = random.choice(choices)
            state["last"] = choice

            log.debug("Playing filler [%s]: %s%s", name, choice, self._elapsed(anchor))
            try:
                with state["lock"]:
                    if state["stop"].is_set():
                        break
                    state["proc"] = subprocess.Popen(["paplay", choice])
                state["proc"].wait()
            except Exception as e:
                log.error("Filler playback failed for %s: %s", choice, e)
            finally:
                with state["lock"]:
                    state["proc"] = None

            if state["stop"].wait(cfg["interval"]):
                break

    # ------------------------------------------------------------------
    # Playback
    # ------------------------------------------------------------------

    def _play_file(self, path: str) -> None:
        """Play a local audio file. mp3 is decoded via mpg123 first, since
        paplay cannot reliably play some mp3 (e.g. Piper/Wyoming output)
        through to completion -- it will silently stop partway through
        despite the file being complete on disk."""
        ext = os.path.splitext(path)[1].lower()
        if ext == ".mp3":
            wav_tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
            wav_tmp.close()
            try:
                result = subprocess.run(
                    ["mpg123", "-q", "-w", wav_tmp.name, path],
                    check=False,
                )
                if (
                    result.returncode != 0
                    or not os.path.exists(wav_tmp.name)
                    or os.path.getsize(wav_tmp.name) == 0
                ):
                    log.error(
                        "mpg123 decode failed for %s (rc=%s); "
                        "falling back to direct paplay of mp3",
                        path,
                        result.returncode,
                    )
                    subprocess.run(["paplay", path], check=False)
                else:
                    subprocess.run(["paplay", wav_tmp.name], check=False)
            finally:
                try:
                    os.unlink(wav_tmp.name)
                except OSError:
                    pass
        else:
            subprocess.run(["paplay", path], check=False)

    def play(
        self,
        url: Union[str, List[str]],
        done_callback: Optional[Callable[[], None]] = None,
        stop_first: bool = False,
    ) -> None:
        if isinstance(url, str):
            urls = [url]
        else:
            urls = list(url)

        self._done_callback = done_callback
        log.info("Playing via paplay: %s", urls)

        for u in urls:
            local_path = u
            tmp_file = None
            is_processing_sound = os.path.basename(u) == PROCESSING_SOUND_BASENAME
            is_url = u.startswith("http://") or u.startswith("https://")

            if not is_processing_sound:
                self._stop_stage("thinking")

            try:
                if is_url:
                    suffix = ".mp3" if ".mp3" in u else ".wav"
                    tmp_file = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
                    tmp_file.close()
                    log.info("Downloading TTS to %s", tmp_file.name)

                    self._download_start = time.monotonic() if DEBUG_TIMING else None
                    self._start_stage("fetching")

                    last_size = -1
                    stable_rounds = 0
                    for _ in range(30):
                        urlretrieve(u, tmp_file.name)
                        size = os.path.getsize(tmp_file.name)
                        if size > 0 and size == last_size:
                            stable_rounds += 1
                            if stable_rounds >= 2:
                                break
                        else:
                            stable_rounds = 0
                        last_size = size
                        time.sleep(0.5)

                    self._stop_stage("fetching")

                    local_path = tmp_file.name
                    log.info(
                        "TTS download complete, size=%d%s",
                        last_size,
                        self._elapsed(self._download_start),
                    )

                self._play_file(local_path)

                if is_processing_sound:
                    self._turn_start = time.monotonic() if DEBUG_TIMING else None
                    self._start_stage("thinking")

                silence = "/app/sounds/custom/silence_400ms.wav"
                if os.path.exists(silence):
                    subprocess.run(["paplay", silence], check=False)
                else:
                    time.sleep(0.75)

            except Exception as e:
                log.error("paplay failed for %s: %s", u, e)
                self._stop_stage("fetching")
            finally:
                if tmp_file is not None:
                    try:
                        os.unlink(tmp_file.name)
                    except OSError:
                        pass

        if self._done_callback:
            cb = self._done_callback
            self._done_callback = None
            cb()

    def stop(self) -> None:
        self._stop_all_stages()
        subprocess.run(["pkill", "-f", "paplay"], check=False)

    def set_volume(self, volume: float) -> None:
        log.debug("set_volume(%.1f) ignored by paplay backend", volume)

    def duck(self, factor: float = 0.5) -> None:
        log.debug("duck(%.2f) ignored", factor)

    def unduck(self) -> None:
        log.debug("unduck() ignored")
        self._stop_all_stages()

    @property
    def is_playing(self) -> bool:
        return False
