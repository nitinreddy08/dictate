#!/usr/bin/env python3
"""Generate the soft start/stop chimes (run with the dictate venv: .venv/bin/python sounds/make_sounds.py)."""
import os
import wave

import numpy as np

RATE = 48000
VOLUME = 0.16  # gentle: well below full scale
HERE = os.path.dirname(os.path.abspath(__file__))


def note(freq, start, length, total):
    """A bell-like tone: quick fade-in, smooth exponential fade-out, a touch of octave shimmer."""
    t = np.arange(int(length * RATE)) / RATE
    env = np.minimum(t / 0.006, 1.0) * np.exp(-t / (length / 4.5))
    tone = np.sin(2 * np.pi * freq * t) + 0.18 * np.sin(2 * np.pi * freq * 2 * t)
    out = np.zeros(int(total * RATE))
    i = int(start * RATE)
    out[i:i + len(t)] += tone * env
    return out


def chime(freqs, path):
    total = 0.32
    sig = sum(note(f, i * 0.085, 0.22, total) for i, f in enumerate(freqs))
    sig = sig / np.abs(sig).max() * VOLUME
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes((sig * 32767).astype(np.int16).tobytes())


chime([659.25, 987.77], os.path.join(HERE, "start.wav"))  # E5 -> B5, rising: "I'm listening"
chime([987.77, 659.25], os.path.join(HERE, "stop.wav"))   # B5 -> E5, falling: "got it"
print("wrote start.wav and stop.wav")
