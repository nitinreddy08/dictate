"""The speech-to-text model: loaded while you speak, freed again when unused."""
import gc
import os
import sys
import threading
import time

from .config import vocabulary
from .text import STYLE


def default_threads():
    """Physical cores (capped at 8): on a test laptop, hyperthreads made Whisper ~40% slower."""
    try:
        import psutil

        cores = psutil.cpu_count(logical=False)
    except ImportError:
        cores = None
    return max(1, min(8, cores or (os.cpu_count() or 2) // 2))


class Model:
    """A faster-whisper model that loads on demand and is freed after `idle_secs` without use.

    `name` is a catalog key (e.g. "small.en"); if `model_dir/name` exists it's used as-is, otherwise
    the model is downloaded into `model_dir`. `on_release` runs after the model is freed.
    """

    def __init__(self, name, model_dir, threads=None, idle_secs=90, on_release=None, log=print):
        self.name, self.model_dir = name, model_dir
        self.threads = threads or default_threads()
        self.idle_secs, self.on_release, self.log = idle_secs, on_release, log
        self.model, self.timer = None, None
        self.lock = threading.Lock()
        self.ready = threading.Event()

    def preload(self):
        if self.timer:
            self.timer.cancel()
        threading.Thread(target=self.get, daemon=True).start()

    def get(self):
        with self.lock:
            if self.model is None:
                from faster_whisper import WhisperModel

                t = time.time()
                local = os.path.join(self.model_dir, self.name)
                self.model = WhisperModel(local if os.path.isdir(local) else self.name, device="cpu",
                                          compute_type="int8", cpu_threads=self.threads,
                                          download_root=self.model_dir)
                self.log(f"loaded {self.name} in {time.time() - t:.1f}s")
                self.ready.set()
            return self.model

    def transcribe(self, audio, context=""):
        segments, _ = self.get().transcribe(
            audio, beam_size=5, vad_filter=False, without_timestamps=True,
            condition_on_previous_text=False, hotwords=vocabulary(),
            initial_prompt=f"{STYLE} {context[-200:]}".strip())
        return " ".join(s.text.strip() for s in segments).strip()

    def release_later(self):
        self.timer = threading.Timer(self.idle_secs, self.release)
        self.timer.daemon = True
        self.timer.start()

    def release(self):
        with self.lock:
            self.model = None
            self.ready.clear()
            gc.collect()
            if sys.platform.startswith("linux"):
                import ctypes

                ctypes.CDLL("libc.so.6").malloc_trim(0)  # hand freed memory back to the OS
        self.log("idle: model unloaded")
        if self.on_release:
            self.on_release()
