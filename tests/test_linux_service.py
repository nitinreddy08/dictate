"""End to end through the real Linux service (dictate.py serve): socket -> recording -> transcription ->
typing. The microphone, keyboard, popup and GNOME are faked, so nothing reaches your desktop."""
import os
import socket
import stat
import sys
import tempfile
import threading
import time
import wave

import numpy as np
import pytest

from conftest import SENTENCE

pytestmark = pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux service")


@pytest.fixture
def service(monkeypatch, speech, tmp_path):
    import dictate

    run = tempfile.mkdtemp(prefix="dt-")  # short path: Unix sockets are limited to ~100 characters
    clip = tmp_path / "clip.wav"
    with wave.open(str(clip), "wb") as w:
        pcm = (np.concatenate([np.zeros(int(0.4 * 16000), np.float32), speech]) * 32767).astype(np.int16)
        w.setnchannels(1), w.setsampwidth(2), w.setframerate(16000), w.writeframes(pcm.tobytes())
    # A fake pw-record: writes the clip to the requested file, then waits to be stopped
    bindir = tmp_path / "bin"
    bindir.mkdir()
    fake = bindir / "pw-record"
    fake.write_text(f'#!/bin/sh\nfor a; do out="$a"; done\ncp "{clip}" "$out"\nexec sleep 60\n')
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{bindir}:{os.environ['PATH']}")

    typed, popup = [], []
    for name, value in {
        "SOCK": os.path.join(run, "s"), "RUNTIME": run, "STATE": os.path.join(run, "state"),
        "HISTORY": os.path.join(run, "history.jsonl"), "RECOVERED": os.path.join(run, "recovered.wav"),
        "SOUNDS": False, "SOCKET_ACTIVATED": False,
        "mic_status": lambda: (None, "Test mic"), "chime": lambda name: None,
        "extension_loaded": lambda: True, "type_via_extension": lambda text: typed.append(text) or "commit",
        "enter_finishes": lambda on: None, "setup_enter_key": lambda: None,
    }.items():
        monkeypatch.setattr(dictate, name, value)
    monkeypatch.setattr(dictate.OSD, "send", lambda self, cmd: popup.append(cmd))
    monkeypatch.setattr(dictate.signal, "signal", lambda *a: None)  # serve() runs in a thread here
    threading.Thread(target=dictate.serve, daemon=True).start()
    for _ in range(50):
        if os.path.exists(dictate.SOCK):
            break
        time.sleep(0.1)

    def send(cmd):
        s = socket.socket(socket.AF_UNIX)
        s.connect(dictate.SOCK)
        s.sendall(cmd.encode())
        s.close()

    return send, typed, popup, dictate


def test_dictation_end_to_end(service):
    send, typed, popup, dictate = service
    send("toggle")
    time.sleep(4)
    send("toggle")
    for _ in range(100):
        if typed:
            break
        time.sleep(0.1)
    assert typed, f"nothing typed; popup said {popup}"
    words = {w.strip(".,").lower() for w in typed[0].split()}
    assert len(words & {w.strip(".,").lower() for w in SENTENCE.split()}) >= 6, typed[0]
    assert any(c.startswith("listening Test mic") for c in popup)
    assert any(c.startswith("done ") for c in popup)
    assert [e["text"] for e in dictate.history.recent(dictate.HISTORY)] == [typed[0].strip()]
