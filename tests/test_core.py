import threading
import time

import numpy as np

from conftest import SENTENCE
from dictate_core import RATE, history
from dictate_core.config import vocabulary
from dictate_core.session import Transcription
from dictate_core.text import apply_commands, clean


def test_voice_commands():
    assert apply_commands("Hello, comma, how are you question mark") == "Hello, how are you?"
    assert apply_commands("Dear team new line thanks for the update full stop") == "Dear team\nThanks for the update."
    assert apply_commands("First point. New paragraph. second point") == "First point.\n\nSecond point"
    assert apply_commands("Note colon buy milk") == "Note: buy milk"


def test_clean_drops_silence_phrases_only_when_quiet():
    assert clean("Thank you.", peak=0.005) == ""
    assert clean("Thank you.", peak=0.3) == "Thank you."


def test_history_keeps_only_the_newest_three(tmp_path):
    path = str(tmp_path / "history.jsonl")
    for i in range(1, 8):
        history.remember(path, f"dictation {i}")
    assert [e["text"] for e in history.recent(path)] == ["dictation 7", "dictation 6", "dictation 5"]
    history.clear(path)
    assert history.recent(path) == []


def test_vocabulary_ignores_comments(tmp_path):
    f = tmp_path / "vocabulary.txt"
    f.write_text("# comment\nRahul\n\n  Bengaluru \n# pull request\n", encoding="utf-8")
    assert vocabulary(str(f)) == "Rahul, Bengaluru"
    assert vocabulary(str(tmp_path / "missing.txt")) is None


def words(text):
    return {w.strip(".,?!").lower() for w in text.split()}


def test_transcribes_speech(model, speech):
    text = model.transcribe(speech)
    assert len(words(text) & words(SENTENCE)) >= 6, text


class Growing:
    """Audio that grows in real time, like a microphone."""

    def __init__(self, audio):
        self.audio, self.n, self.lock = audio, 0, threading.Lock()

    def feed(self, seconds_per_step=0.1):
        step = int(RATE * seconds_per_step)
        while self.n < len(self.audio):
            with self.lock:
                self.n = min(self.n + step, len(self.audio))
            time.sleep(seconds_per_step)

    def get(self):
        with self.lock:
            return self.audio[:self.n]


def test_text_is_ready_when_stopping_after_a_pause(model, speech):
    model.get()
    audio = np.concatenate([speech, np.zeros(int(2.5 * RATE), np.float32)])  # talk, then pause 2.5 s
    mic = Growing(audio)
    t = Transcription(model, mic.get)
    mic.feed()
    start = time.time()
    text = t.finish(audio)
    assert time.time() - start < 0.5, "should already be transcribed during the pause"
    assert len(words(text) & words(SENTENCE)) >= 6, text


def test_silence_never_reaches_whisper(model):
    noise = np.random.default_rng(0).normal(0, 0.001, 4 * RATE).astype(np.float32)
    t = Transcription(model, lambda: noise)
    calls = []
    real, model.transcribe = model.transcribe, lambda *a, **k: calls.append(1) or ""
    try:
        assert t.finish(noise) == ""
    finally:
        model.transcribe = real
    assert calls == []
