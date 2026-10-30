"""Microphone recording into memory (nothing is written to disk unless transcription fails)."""
import threading
import wave

import numpy as np

from dictate_core import RATE


class MicError(Exception):
    """Title|hint, shown in the popup."""


def default_mic():
    """Name of the default input device; raises MicError if there is none."""
    import sounddevice as sd

    try:
        return sd.query_devices(kind="input")["name"]
    except Exception:
        raise MicError("No microphone found|Connect one, or pick an input in Settings → System → Sound")


class Recorder:
    """Records from the default mic. `get()` returns everything so far as 16 kHz float32 mono;
    `on_level(0..1)` is called about 20 times a second for the waveform."""

    def __init__(self, on_level=None):
        self.on_level = on_level
        self.blocks, self.lock = [], threading.Lock()
        self.rate, self.stream = RATE, None

    def start(self):
        import sounddevice as sd

        try:
            self.stream = self._open(sd, RATE)
        except Exception:
            try:  # some drivers only offer their native rate: record at that and convert
                native = int(sd.query_devices(kind="input")["default_samplerate"])
                self.stream = self._open(sd, native)
            except Exception as ex:
                raise MicError(f"Couldn't open the microphone|{str(ex)[:80]}")
        self.stream.start()

    def _open(self, sd, rate):
        self.rate = rate
        return sd.InputStream(samplerate=rate, channels=1, dtype="float32", blocksize=rate // 20,
                              callback=self._callback)

    def _callback(self, indata, frames, time_info, status):
        block = indata[:, 0].copy()
        with self.lock:
            self.blocks.append(block)
        if self.on_level:
            rms = float(np.sqrt(np.mean(block * block)))
            self.on_level(min(1.0, (rms * 4.5) ** 0.8))

    def get(self):
        with self.lock:
            audio = np.concatenate(self.blocks) if self.blocks else np.zeros(0, np.float32)
        if self.rate != RATE and audio.size:
            n = int(len(audio) * RATE / self.rate)
            audio = np.interp(np.linspace(0, len(audio) - 1, n), np.arange(len(audio)), audio).astype(np.float32)
        return audio

    def stop(self):
        if self.stream:
            self.stream.stop()
            self.stream.close()
            self.stream = None
        return self.get()


class ClipRecorder(Recorder):
    """Plays a WAV file in real time instead of using a microphone (tests and demos: DICTATE_FAKE_MIC)."""

    def __init__(self, path, on_level=None):
        super().__init__(on_level)
        from dictate_core.wavio import read_16k

        self.clip, self._stop = read_16k(path), threading.Event()

    def start(self):
        def feed():
            step = RATE // 20
            for i in range(0, len(self.clip), step):
                if self._stop.wait(0.05):
                    return
                self._callback(self.clip[i:i + step, None], step, None, None)

        threading.Thread(target=feed, daemon=True).start()

    def stop(self):
        self._stop.set()
        return self.get()


def save_wav(path, audio):
    pcm = (np.clip(audio, -1, 1) * 32767).astype(np.int16)
    with wave.open(path, "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(RATE)
        f.writeframes(pcm.tobytes())
