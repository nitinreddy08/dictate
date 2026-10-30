"""The Windows app end to end: shortcut command -> recording -> transcription -> typing -> history.

The microphone and keyboard are replaced by the app's own test hooks (DICTATE_FAKE_MIC plays a WAV file,
DICTATE_FAKE_TYPE writes typed text to a file), so this also runs on Linux with an off-screen display.
On Windows the real tray icon, popup and hotkey thread start too.
"""
import json
import os
import subprocess
import sys
import time
import wave

import numpy as np
import pytest

from conftest import ROOT, SENTENCE

pytest.importorskip("PySide6")


def send(cmd):
    """The same way `dictate toggle` reaches the running app (a named pipe on Windows)."""
    from dictate_cli.cli import send as cli_send

    assert cli_send(cmd), "app isn't listening"


@pytest.fixture
def app(tmp_path, speech):
    clip = tmp_path / "mic.wav"
    pcm = (np.concatenate([np.zeros(int(0.5 * 16000), np.float32), speech]) * 32767).astype(np.int16)
    with wave.open(str(clip), "wb") as w:
        w.setnchannels(1), w.setsampwidth(2), w.setframerate(16000), w.writeframes(pcm.tobytes())
    typed = tmp_path / "typed.txt"
    env = dict(os.environ, DICTATE_FAKE_MIC=str(clip), DICTATE_FAKE_TYPE=str(typed), PYTHONPATH=ROOT,
               APPDATA=str(tmp_path / "config"), LOCALAPPDATA=str(tmp_path / "data"),
               XDG_CONFIG_HOME=str(tmp_path / "config"), XDG_DATA_HOME=str(tmp_path / "data"))
    if sys.platform != "win32":
        env["QT_QPA_PLATFORM"] = "offscreen"
    # Use the dev machine's small.en if it's there; otherwise (cloud tests) the small, quick tiny.en
    models = tmp_path / "data" / ("Dictate" if sys.platform == "win32" else "dictate") / "models"
    models.mkdir(parents=True)
    local = os.path.join(ROOT, "models", "small.en")
    name = "small.en" if os.path.isdir(local) else "tiny.en"
    if name == "small.en":
        os.symlink(local, models / "small.en")
    cfg = tmp_path / "config" / ("Dictate" if sys.platform == "win32" else "dictate")
    cfg.mkdir(parents=True)
    (cfg / "settings.json").write_text(json.dumps({"model": name, "sounds": False}))

    log = open(tmp_path / "app.log", "w")
    proc = subprocess.Popen([sys.executable, "-m", "dictate_win"], env=env, stdout=log, stderr=subprocess.STDOUT)
    for _ in range(100):
        if "ready" in (tmp_path / "app.log").read_text():
            break
        time.sleep(0.1)
    yield typed, tmp_path
    try:
        send("quit")
        proc.wait(10)
    except Exception:
        proc.kill()
    print((tmp_path / "app.log").read_text())


def test_dictation_types_text_and_remembers_it(app):
    typed, tmp = app
    send("toggle")
    log = tmp / "app.log"
    for _ in range(100):  # wait until the app is actually recording
        if "recording" in log.read_text():
            break
        time.sleep(0.1)
    time.sleep(5)  # the clip plays in real time, then a pause
    send("toggle")
    for _ in range(150):
        if typed.exists() and typed.read_text().strip():
            break
        time.sleep(0.1)
    text = typed.read_text() if typed.exists() else ""
    assert text, (tmp / "app.log").read_text()
    words = {w.strip(".,").lower() for w in text.split()}
    assert len(words & {w.strip(".,").lower() for w in SENTENCE.split()}) >= 6, text
    hist = next((tmp / "data").rglob("history.jsonl"))
    assert json.loads(hist.read_text().splitlines()[-1])["text"] == text.strip()
