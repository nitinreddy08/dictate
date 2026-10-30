#!/usr/bin/env python3
"""Push-to-talk dictation: press a shortcut, speak, press again -> text is typed at the cursor.

  dictate.py serve    run the daemon (started on demand by dictate.socket; exits when idle)
  dictate.py toggle   start/stop recording (bound to Insert)
  dictate.py stop     stop recording if active (bound to Enter, only while listening)
  dictate.py retry    transcribe the last recording that failed or was cut off
"""
import glob
import itertools
import os
import re
import signal
import socket
import subprocess
import sys
import threading
import time

from dictate_core import history
from dictate_core.engine import Model as CoreModel
from dictate_core.session import Transcription
from dictate_core.text import clean

RUNTIME = os.environ.get("XDG_RUNTIME_DIR", "/tmp")  # per-user tmpfs: audio never touches the disk
SOCK = os.path.join(RUNTIME, "dictate.sock")
STATE = os.path.join(RUNTIME, "dictate.state")  # read by the top-bar button (gnome-extension/)
# Backups, all in RAM (cleared at logout): your recent dictations, and the last recording that failed
HISTORY = os.path.join(RUNTIME, "dictate-history.jsonl")
RECOVERED = os.path.join(RUNTIME, "dictate.recovered.wav")
HISTORY_KEEP = 3
HERE = os.path.dirname(os.path.abspath(__file__))
# A local model folder (models/<name>) is used if present, otherwise the name is downloaded to MODEL_DIR
MODEL = os.environ.get("DICTATE_MODEL", "small.en")
MODEL_DIR = os.environ.get("DICTATE_MODEL_DIR", os.path.join(HERE, "models"))
SOCKET_ACTIVATED = os.environ.get("LISTEN_FDS") == "1"
SOUNDS = os.environ.get("DICTATE_SOUNDS", "1") != "0"  # soft start/stop chimes (sounds/make_sounds.py)
CHIME_SKIP = 0.35  # seconds of audio dropped at the start so the start chime isn't transcribed
MAX_SECS = float(os.environ.get("DICTATE_MAX_SECS", "300"))  # auto-finish a forgotten recording
MIN_RECORDING = 0.6  # seconds; a second press sooner than this is ignored
IDLE_SECS = float(os.environ.get("DICTATE_IDLE_SECS", "90"))
THREADS = int(os.environ.get("DICTATE_THREADS", "4"))  # physical cores; hyperthreads made it slower
RATE = 16000

# The top-bar extension (gnome-extension/dictate@local) types text and owns the Enter key when loaded
EXT = ["busctl", "--user", "--timeout=3", "--", "call", "org.gnome.Shell",
       "/org/gnome/Shell/Extensions/Dictate", "org.gnome.Shell.Extensions.Dictate"]


def notify(msg, nid=[None]):
    cmd = ["notify-send", "-a", "Dictate", "-p", "-t", "2500", "Dictate", msg]
    if nid[0]:
        cmd[1:1] = ["-r", nid[0]]
    try:
        nid[0] = subprocess.run(cmd, capture_output=True, text=True, timeout=2).stdout.strip() or None
    except Exception:
        pass


class OSD:
    """Bottom-center popup. Drawn by the top-bar extension inside GNOME Shell when it's loaded (sent over
    D-Bus without waiting for a reply, so 20 level updates a second are cheap); otherwise by osd.py, a
    GTK process fed over stdin."""

    def __init__(self, use_extension=lambda: False):
        self.use_extension = use_extension  # asked again after every reset()
        self.ext, self.bus, self.proc = None, None, None
        self.lock = threading.Lock()

    def reset(self):
        """Forget whether the extension is there (checked again on the next update). Called at the start
        of every dictation, so a GNOME Shell restart or a new login is picked up straight away."""
        with self.lock:
            if self.bus:
                self.bus.close()
            self.bus, self.ext = None, None

    def send(self, cmd):
        with self.lock:
            if self.ext is None:
                self.ext = self.use_extension()
            if self.ext and self._send_dbus(cmd):
                return
            self._send(cmd)

    def close(self):
        with self.lock:
            if self.proc and self.proc.poll() is None:
                self.proc.stdin.close()  # osd.py exits on EOF
                self.proc.wait(timeout=3)
            self.proc = None
            if self.bus:
                self.bus.close()
            self.bus, self.ext = None, None

    def _send_dbus(self, cmd):
        from jeepney import DBusAddress, MessageFlag, new_method_call
        from jeepney.io.blocking import open_dbus_connection

        msg = new_method_call(DBusAddress("/org/gnome/Shell/Extensions/Dictate", bus_name="org.gnome.Shell",
                                          interface="org.gnome.Shell.Extensions.Dictate"), "Osd", "s", (cmd,))
        msg.header.flags |= MessageFlag.no_reply_expected
        for attempt in range(2):  # a dropped connection (e.g. after re-login) gets one fresh retry
            try:
                if self.bus is None:
                    self.bus = open_dbus_connection(bus="SESSION")
                self.bus.send(msg)
                return True
            except Exception as ex:
                if self.bus:
                    self.bus.close()
                self.bus = None
                if attempt:
                    print(f"osd over D-Bus failed, using osd.py: {ex}", flush=True)
        self.ext = False
        return False

    def _send(self, cmd):
        try:
            if self.proc is None or self.proc.poll() is not None:
                self.proc = subprocess.Popen(["/usr/bin/python3", os.path.join(HERE, "osd.py")],
                                             stdin=subprocess.PIPE, text=True)
            self.proc.stdin.write(cmd.replace("\n", " ") + "\n")
            self.proc.stdin.flush()
        except Exception as ex:
            print(f"osd failed: {ex}", flush=True)
            notify(cmd)


# --- microphone -------------------------------------------------------------------

def mic_status():
    """(problem or None, mic name). Checks the default input before recording starts."""
    try:
        vol = subprocess.run(["wpctl", "get-volume", "@DEFAULT_AUDIO_SOURCE@"],
                             capture_output=True, text=True, timeout=2)
    except (OSError, subprocess.TimeoutExpired):
        return None, ""  # can't tell; try recording anyway
    if vol.returncode != 0 or not vol.stdout.startswith("Volume"):
        return "No microphone found|Connect one, or pick an input in Settings → Sound", ""
    if "[MUTED]" in vol.stdout:
        return "Your microphone is muted|Unmute it from the sound menu in the top bar", ""
    if float(vol.stdout.split()[1]) < 0.05:
        return "Your microphone volume is at zero|Raise it in Settings → Sound", ""
    info = subprocess.run(["wpctl", "inspect", "@DEFAULT_AUDIO_SOURCE@"],
                          capture_output=True, text=True, timeout=2).stdout
    m = re.search(r'node\.description = "([^"]*)"', info)
    return None, m.group(1) if m else ""


def meter(path, osd, stop):
    """While recording, send the mic level of the newest ~60 ms of audio to the OSD."""
    import numpy as np

    while not stop.wait(0.05):
        try:
            with open(path, "rb") as f:
                f.seek(0, 2)
                size = f.tell()
                if size < 44 + 1920:
                    continue
                f.seek(size - 1920 - (size % 2))
                pcm = np.frombuffer(f.read(1920), dtype=np.int16).astype(np.float32) / 32768.0
        except OSError:
            continue
        rms = float(np.sqrt(np.mean(pcm * pcm)))
        osd.send(f"level {min(1.0, (rms * 4.5) ** 0.8):.3f}")


def chime(name):
    if SOUNDS:
        subprocess.Popen(["pw-play", os.path.join(HERE, "sounds", f"{name}.wav")],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def load_wav(path):
    """16 kHz mono s16 WAV -> float32 array, minus the chime (skips PyAV; header may be unfinished)."""
    import numpy as np

    try:
        data = open(path, "rb").read()
    except OSError:
        return np.zeros(0, dtype=np.float32)
    i = data.find(b"data")
    pcm = data[i + 8:] if i >= 0 else data[44:]
    pcm = pcm[: len(pcm) // 2 * 2]
    audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
    return audio[int(CHIME_SKIP * RATE):] if SOUNDS else audio


# --- speech to text (shared with Windows: dictate_core) -----------------------------

class Recording:
    """One dictation: pw-record writes to a RAM file while dictate_core transcribes it as it grows."""

    def __init__(self, model, path):
        self.path = path
        self.proc = subprocess.Popen(
            ["pw-record", "--rate", str(RATE), "--channels", "1", "--format", "s16", path],
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        self.transcription = Transcription(model, lambda: load_wav(path))

    def failed(self):
        """pw-record's error if it couldn't open the microphone, else None."""
        if self.proc.poll() is None or self.transcription.stop_flag.is_set():
            return None
        return (self.proc.stderr.read() or b"").decode(errors="replace").strip() or "recorder exited"

    def finish(self):
        """Stop recording and return (raw text, audio) once anything still untranscribed is done."""
        self.proc.terminate()
        self.proc.wait()
        audio = load_wav(self.path)
        return self.transcription.finish(audio), audio


# --- typing ------------------------------------------------------------------------

def extension_loaded():
    try:
        return subprocess.run(EXT + ["Ping"], capture_output=True, timeout=3).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def type_via_extension(text):
    """Insert text through the extension. Returns how: "commit" (typed into the text field), "paste"
    (pasted; also left on the clipboard), "clipboard" (no text field was active: only copied), or None."""
    try:
        out = subprocess.run(EXT + ["Type", "s", text], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return None
    m = re.search(r'"(commit|paste|clipboard)"', out.stdout)
    return m.group(1) if out.returncode == 0 and m else None


def remember(text):
    """Add a dictation to the recent history (top-bar icon → right-click)."""
    history.remember(HISTORY, text, HISTORY_KEEP)


class Typer:
    """Fallback when the extension isn't loaded: types through a virtual uinput keyboard (US layout)."""

    def __init__(self):
        from evdev import UInput, ecodes as e

        self.e = e
        self.keys = {}
        for c in "abcdefghijklmnopqrstuvwxyz":
            self.keys[c] = (getattr(e, f"KEY_{c.upper()}"), False)
            self.keys[c.upper()] = (getattr(e, f"KEY_{c.upper()}"), True)
        for c in "0123456789":
            self.keys[c] = (getattr(e, f"KEY_{c}"), False)
        for c, k in zip(")!@#$%^&*(", "0123456789"):
            self.keys[c] = (getattr(e, f"KEY_{k}"), True)
        for plain, shifted, name in [
            (" ", None, "SPACE"), ("\n", None, "ENTER"), ("\t", None, "TAB"),
            ("-", "_", "MINUS"), ("=", "+", "EQUAL"), ("[", "{", "LEFTBRACE"),
            ("]", "}", "RIGHTBRACE"), ("\\", "|", "BACKSLASH"), (";", ":", "SEMICOLON"),
            ("'", '"', "APOSTROPHE"), ("`", "~", "GRAVE"), (",", "<", "COMMA"),
            (".", ">", "DOT"), ("/", "?", "SLASH"),
        ]:
            code = getattr(e, f"KEY_{name}")
            self.keys[plain] = (code, False)
            if shifted:
                self.keys[shifted] = (code, True)
        codes = {k for k, _ in self.keys.values()} | {e.KEY_LEFTSHIFT}

        # Skip evdev's read-back of the new device node: we only write, and the lookup
        # retries for ~2 s because /dev/input/event* isn't readable by the user.
        class WriteOnlyUInput(UInput):
            def _find_device(self, fd):
                return None

        self.ui = WriteOnlyUInput({e.EV_KEY: sorted(codes)}, name="dictate-keyboard")
        time.sleep(0.5)  # let the compositor pick up the new device

    def _key(self, code, val):
        self.ui.write(self.e.EV_KEY, code, val)
        self.ui.syn()

    def type(self, text):
        # Smart punctuation from Whisper -> plain ASCII
        for a, b in {"’": "'", "‘": "'", "“": '"', "”": '"', "–": "-", "—": "-", "…": "..."}.items():
            text = text.replace(a, b)
        for ch in text:
            if ch not in self.keys:
                continue
            code, shift = self.keys[ch]
            if shift:
                self._key(self.e.KEY_LEFTSHIFT, 1)
            self._key(code, 1)
            self._key(code, 0)
            if shift:
                self._key(self.e.KEY_LEFTSHIFT, 0)
            time.sleep(0.004)


# --- Enter key (fallback when the extension isn't loaded) ---------------------------

ENTER_KEY = "/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/dictate-enter/"


def gset(*args):
    subprocess.run(["gsettings", "set", *args], capture_output=True, timeout=3)


def enter_finishes(on):
    """Grab Enter only while recording, so it can finish dictation without breaking Enter elsewhere."""
    gset(f"org.gnome.settings-daemon.plugins.media-keys.custom-keybinding:{ENTER_KEY}",
         "binding", "Return" if on else "")


def setup_enter_key():
    import ast

    out = subprocess.run(["gsettings", "get", "org.gnome.settings-daemon.plugins.media-keys",
                          "custom-keybindings"], capture_output=True, text=True).stdout.strip()
    paths = ast.literal_eval(out.removeprefix("@as ")) if out else []
    if ENTER_KEY not in paths:
        gset("org.gnome.settings-daemon.plugins.media-keys", "custom-keybindings", str(paths + [ENTER_KEY]))
    schema = f"org.gnome.settings-daemon.plugins.media-keys.custom-keybinding:{ENTER_KEY}"
    gset(schema, "name", "Dictate: finish with Enter (active only while listening)")
    gset(schema, "command", f"{sys.executable} {os.path.abspath(__file__)} stop")
    enter_finishes(False)


def set_state(state):
    """listening | transcribing | idle, for the top-bar icon (which also grabs Enter while listening)."""
    try:
        with open(STATE, "w") as f:
            f.write(state)
    except OSError:
        pass


def cleanup():
    """Release Enter and rescue audio left by a crash (also run by systemd after any stop)."""
    enter_finishes(False)
    set_state("idle")
    # A recording cut off by a crash is kept for "Retry last recording"; anything else is deleted
    stale = sorted(glob.glob(os.path.join(RUNTIME, "dictate-*.wav")), key=os.path.getmtime)
    if stale and os.path.getsize(stale[-1]) > RATE * 2:  # more than ~1 s of audio
        os.replace(stale.pop(), RECOVERED)
        print("cleanup: kept an interrupted recording for retry", flush=True)
    for f in stale:
        os.unlink(f)
    print("cleanup: Enter released", flush=True)


# --- daemon ------------------------------------------------------------------------

def serve():
    import atexit

    t0 = time.time()
    cleanup()
    atexit.register(enter_finishes, False)
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    if SOCKET_ACTIVATED:
        srv = socket.socket(fileno=3)  # handed over by systemd (dictate.socket)
    else:
        if os.path.exists(SOCK):
            os.unlink(SOCK)
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        srv.bind(SOCK)
        srv.listen(1)

    # Slow setup runs in the background so the first key press starts recording right away
    has_ext = [False]
    ext_ready = threading.Thread(target=lambda: has_ext.__setitem__(0, extension_loaded()), daemon=True)
    enter_ready = threading.Thread(target=setup_enter_key, daemon=True)
    ext_ready.start()
    osd = OSD(lambda: (ext_ready.join(), has_ext[0])[1])

    def recheck_extension():
        """At the start of each dictation: is the extension (still) there? GNOME may have restarted."""
        nonlocal ext_ready
        if not ext_ready.is_alive():
            ext_ready = threading.Thread(target=lambda: has_ext.__setitem__(0, extension_loaded()), daemon=True)
            ext_ready.start()
        osd.reset()
    def on_release():
        osd.close()
        if SOCKET_ACTIVATED:  # systemd keeps the socket and restarts us on the next key press
            print("idle: exiting", flush=True)
            os.kill(os.getpid(), signal.SIGTERM)

    model = CoreModel(MODEL, MODEL_DIR, threads=THREADS, idle_secs=IDLE_SECS, on_release=on_release,
                      log=lambda m: print(m, flush=True))
    enter_ready.start()
    typer = []

    def type_text(text):
        ext_ready.join()
        if has_ext[0] and (how := type_via_extension(text)):
            return how
        if not typer:
            typer.append(Typer())
        typer[0].type(text)
        return "keys"

    def deliver(text, peak):
        print(f"transcribed {len(text.split())} words", flush=True)
        if text:
            remember(text)
            how = type_text(text if text.endswith("\n") else text + " ")
            osd.send(f"{'nofield' if how == 'clipboard' else 'done'} {text}")
            return True
        if peak < 0.002:
            osd.send("error The microphone only sent silence|Check your input device in Settings → Sound")
        else:
            osd.send("empty")
        return False

    def retry():
        """Transcribe the last recording that failed or was cut off, then deliver it as usual."""
        if not os.path.exists(RECOVERED):
            osd.send("error Nothing to retry|There's no failed recording waiting")
            return
        osd.send("transcribing")
        set_state("transcribing")
        try:
            audio = load_wav(RECOVERED)
            text = clean(model.transcribe(audio), float(abs(audio).max()) if audio.size else 0.0)
        except Exception as ex:
            fail(f"Retry failed|{str(ex)[:80]}")
            return
        finally:
            set_state("idle")
            model.release_later()
        deliver(text, float(abs(audio).max()) if audio.size else 0.0)
        os.unlink(RECOVERED)

    def grab_enter(on):
        ext_ready.join()
        if not has_ext[0]:  # the extension grabs Enter itself, from the state file
            enter_ready.join()
            enter_finishes(on)

    def fail(message):  # "Title|hint"
        print(f"error: {message.split('|')[0]}", flush=True)
        osd.send(f"error {message}")
        set_state("idle")

    names = (f"dictate-{os.getpid()}-{n}.wav" for n in itertools.count())
    rec = None
    while True:
        conn, _ = srv.accept()
        cmd = conn.recv(64).decode().strip()
        conn.close()
        if cmd not in ("toggle", "stop", "retry") or (cmd == "stop" and rec is None):
            continue
        if rec is not None and time.time() - started < MIN_RECORDING:
            continue  # a double-tap or a held key repeating: don't cut the dictation off instantly
        if cmd == "retry":
            if rec is None:
                retry()
            continue

        if rec is None:
            problem, mic = mic_status()
            if problem:
                fail(problem)
                continue
            recheck_extension()
            model.preload()  # loads while you speak
            chime("start")
            rec = Recording(model, os.path.join(RUNTIME, next(names)))
            started = time.time()
            print(f"recording ({time.time() - t0:.2f}s since daemon start)", flush=True)
            osd.send(f"listening {mic}")
            set_state("listening")
            grab_enter(True)
            stop_meter = threading.Event()
            threading.Thread(target=meter, args=(rec.path, osd, stop_meter), daemon=True).start()
            auto_stop = threading.Timer(MAX_SECS, toggle, args=("stop",))
            auto_stop.daemon = True
            auto_stop.start()
            time.sleep(0.3)
            if (err := rec.failed()):
                stop_meter.set()
                auto_stop.cancel()
                grab_enter(False)
                rec = None
                fail(f"Couldn't open the microphone|{err[:80]}")
            continue

        auto_stop.cancel()
        grab_enter(False)
        stop_meter.set()
        chime("stop")
        osd.send("transcribing")
        set_state("transcribing")
        t = time.time()
        failed = False
        try:
            text, audio = rec.finish()
            peak = float(abs(audio).max()) if audio.size else 0.0
            text = clean(text, peak)
        except Exception as ex:
            failed = True
            print(f"transcription failed: {ex}", flush=True)
            fail("Transcription failed|Your recording is saved. Right-click the top-bar icon → Retry")
            continue
        finally:
            if os.path.exists(rec.path):
                if failed:
                    os.replace(rec.path, RECOVERED)  # keep it for "Retry last recording"
                else:
                    os.unlink(rec.path)
            rec = None
            set_state("idle")
            model.release_later()
        print(f"{time.time() - t:.2f}s after stop", flush=True)
        deliver(text, peak)


def toggle(cmd="toggle"):
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        s.connect(SOCK)
    except OSError:
        notify("Dictation is not set up: systemctl --user enable --now dictate.socket")
        sys.exit(1)
    s.sendall(cmd.encode())
    s.close()


if __name__ == "__main__":
    {"serve": serve, "toggle": toggle, "stop": lambda: toggle("stop"), "retry": lambda: toggle("retry"),
     "cleanup": cleanup}.get(
        sys.argv[1] if len(sys.argv) > 1 else "", lambda: print(__doc__))()
