"""Dictate for Windows: lives in the tray and listens when you press your shortcut.

Press the shortcut (Insert by default), speak, press it again or Enter: the text is typed where your
cursor is. Right-click the tray icon for your last 3 dictations, Retry, settings and Quit.

Test hooks (also let the app run on Linux for development):
  DICTATE_FAKE_MIC=clip.wav   play a WAV file instead of using the microphone
  DICTATE_FAKE_TYPE=out.txt   append typed text to a file instead of typing it
"""
import os
import sys
import threading
import time
import traceback

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtGui import QAction, QCursor
from PySide6.QtNetwork import QLocalServer
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from dictate_core import history
from dictate_core.config import data_dir, load_settings, vocabulary_file, VOCABULARY_TEMPLATE
from dictate_core.engine import Model
from dictate_core.session import Transcription
from dictate_core.text import clean
from dictate_core.wavio import read_16k

from .audio import ClipRecorder, MicError, Recorder, default_mic, save_wav
from .ui import Popup, app_icon, render_icon

IS_WINDOWS = sys.platform == "win32"
ASSETS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "dictate_core", "assets")
MAX_SECS = 300       # a forgotten dictation finishes itself after 5 minutes
MIN_RECORDING = 0.6  # seconds; a second press sooner than this is ignored (double-tap, key repeat)
CHIME_SKIP = 0.35    # seconds dropped from the start so the start chime is never transcribed


def server_name():
    return f"dictate-{os.environ.get('USERNAME') or os.environ.get('USER') or 'user'}"


def log(msg):
    print(time.strftime("%H:%M:%S"), msg, flush=True)


class Controller(QObject):
    toggle_requested = Signal()
    stop_requested = Signal()
    level = Signal(float)
    finished = Signal(str, float, object)  # raw text, peak, error (None if fine)

    def __init__(self, settings):
        super().__init__()
        self.settings = settings
        self.dir = data_dir()
        os.makedirs(self.dir, exist_ok=True)
        self.history_file = os.path.join(self.dir, "history.jsonl")
        self.recovered = os.path.join(self.dir, "recovered.wav")
        self.model = Model(settings["model"], os.path.join(self.dir, "models"),
                           idle_secs=settings["idle_unload_secs"], log=log)
        self.popup = Popup(settings["hotkey"])
        self.rec, self.transcription, self.started, self.busy = None, None, 0.0, False
        self.auto_stop = QTimer(self, singleShot=True, interval=MAX_SECS * 1000, timeout=self.stop)

        self.toggle_requested.connect(self.toggle)
        self.stop_requested.connect(self.stop)
        self.level.connect(lambda v: self.popup.command(f"level {v:.3f}"))
        self.finished.connect(self._deliver)

        self.tray = QSystemTrayIcon(app_icon())
        self.tray.setToolTip(f"Dictate · press {settings['hotkey']} to dictate")
        self.tray.activated.connect(self._tray_clicked)
        self.menu = QMenu()
        self.menu.aboutToShow.connect(self._build_menu)
        self.tray.setContextMenu(self.menu)
        self.tray.show()
        self.anim_t0, self.anim_state = time.monotonic(), "idle"
        self.anim = QTimer(self, interval=80, timeout=self._animate_icon)

        self.hotkeys = None
        if IS_WINDOWS:
            from .hotkeys import Hotkeys

            self.hotkeys = Hotkeys(settings["hotkey"], self.toggle_requested.emit, self.stop_requested.emit)
            self.hotkeys.start()
            self.hotkeys.registered.wait(2)
            if self.hotkeys.error:
                self.popup.command(f"error Shortcut unavailable|{self.hotkeys.error}. "
                                   "Run `dictate setup` to pick another")

        # The CLI (`dictate toggle`) and tests talk to the running app through a local socket
        self.server = QLocalServer(self)
        QLocalServer.removeServer(server_name())
        self.server.listen(server_name())
        self.server.newConnection.connect(self._on_connection)

    # --- commands ------------------------------------------------------------------------------
    def _on_connection(self):
        conn = self.server.nextPendingConnection()

        def read():
            cmd = bytes(conn.readAll()).decode().strip()
            {"toggle": self.toggle, "stop": self.stop, "retry": self.retry,
             "quit": QApplication.quit}.get(cmd.split(" ")[0], lambda: None)()
            if cmd.startswith("osd "):  # tests: drive the popup directly
                self.popup.command(cmd[4:])
            conn.disconnectFromServer()

        conn.readyRead.connect(read)

    def toggle(self):
        if self.rec is None:
            self.start()
        else:
            self.stop()

    def start(self):
        if self.busy:
            return  # still transcribing the previous dictation
        fake_mic = os.environ.get("DICTATE_FAKE_MIC")
        try:
            mic = "Test microphone" if fake_mic else default_mic()
            rec = (ClipRecorder(fake_mic, self.level.emit) if fake_mic else Recorder(self.level.emit))
            self.model.preload()  # loads while you speak
            self._chime("start")
            rec.start()
        except MicError as ex:
            self.popup.command(f"error {ex}")
            return
        self.rec, self.started = rec, time.monotonic()
        skip = int(CHIME_SKIP * 16000) if self.settings["sounds"] else 0
        self.transcription = Transcription(self.model, lambda: rec.get()[skip:])
        self.popup.command(f"listening {mic}")
        self._set_state("listening")
        if self.hotkeys:
            self.hotkeys.set_listening(True)
        self.auto_stop.start()
        log("recording")

    def stop(self):
        if self.rec is None or time.monotonic() - self.started < MIN_RECORDING:
            return
        rec, transcription, self.rec = self.rec, self.transcription, None
        self.auto_stop.stop()
        if self.hotkeys:
            self.hotkeys.set_listening(False)
        self._chime("stop")
        self.popup.command("transcribing")
        self._set_state("transcribing")
        self.busy = True
        skip = int(CHIME_SKIP * 16000) if self.settings["sounds"] else 0

        def work():
            audio = rec.stop()[skip:]
            peak = float(abs(audio).max()) if audio.size else 0.0
            try:
                self.finished.emit(transcription.finish(audio), peak, None)
            except Exception as ex:
                traceback.print_exc()
                save_wav(self.recovered, audio)  # kept for "Retry last recording"
                self.finished.emit("", peak, ex)

        threading.Thread(target=work, daemon=True).start()

    def retry(self):
        if self.rec is not None or self.busy or not os.path.exists(self.recovered):
            return
        self.popup.command("transcribing")
        self._set_state("transcribing")
        self.busy = True

        def work():
            audio = read_16k(self.recovered)
            peak = float(abs(audio).max()) if audio.size else 0.0
            try:
                text = self.model.transcribe(audio)
                os.unlink(self.recovered)
                self.finished.emit(text, peak, None)
            except Exception as ex:
                self.finished.emit("", peak, ex)

        threading.Thread(target=work, daemon=True).start()

    def _deliver(self, raw, peak, error):
        self.busy = False
        self._set_state("idle")
        self.model.release_later()
        if error is not None:
            self.popup.command("error Transcription failed|Your recording is saved. "
                               "Right-click the tray icon → Retry last recording")
            return
        text = clean(raw, peak)
        log(f"transcribed {len(text.split())} words")
        if not text:
            self.popup.command("error The microphone only sent silence|Check your input in Settings → "
                               "System → Sound" if peak < 0.002 else "empty")
            return
        history.remember(self.history_file, text)
        out = text if text.endswith("\n") else text + " "
        fake = os.environ.get("DICTATE_FAKE_TYPE")
        if fake:
            with open(fake, "a", encoding="utf-8") as f:
                f.write(out)
            self.popup.command(f"done {text}")
            return
        from .typing import type_text, where_to

        if where_to() == "clipboard" or not type_text(out):
            QApplication.clipboard().setText(text)
            self.popup.command(f"nofield {text}")
        else:
            self.popup.command(f"done {text}")

    # --- tray -------------------------------------------------------------------------------------
    def _tray_clicked(self, reason):
        if reason == QSystemTrayIcon.Trigger:  # left click
            self.toggle()

    def _build_menu(self):
        m = self.menu
        m.clear()
        title = QAction(f"Dictate · {self.settings['hotkey']} to start, Enter to finish", m, enabled=False)
        m.addAction(title)
        m.addAction("Stop dictating" if self.rec else "Start dictating", self.toggle)
        m.addSeparator()
        recent = history.recent(self.history_file)
        m.addAction(QAction("Recent dictations · click to copy" if recent else "Nothing dictated yet",
                            m, enabled=False))
        for entry in recent:
            one = " ".join(entry["text"].split())
            label = one if len(one) <= 56 else one[:55].rstrip() + "…"
            m.addAction(label, lambda t=entry["text"]: self._copy(t))
        if os.path.exists(self.recovered):
            m.addSeparator()
            m.addAction("Retry last recording", self.retry)
        if recent:
            m.addAction("Clear history", lambda: history.clear(self.history_file))
        m.addSeparator()
        m.addAction("Edit vocabulary…", self._open_vocabulary)
        if IS_WINDOWS:
            from . import autostart

            start = QAction("Start with Windows", m, checkable=True, checked=autostart.is_enabled())
            start.toggled.connect(autostart.set_enabled)
            m.addAction(start)
        m.addAction("Quit Dictate", QApplication.quit)

    def _copy(self, text):
        QApplication.clipboard().setText(text)
        self.popup.command(f"copied {text}")

    def _open_vocabulary(self):
        path = vocabulary_file()
        if not os.path.exists(path):
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write(VOCABULARY_TEMPLATE)
        if IS_WINDOWS:
            os.startfile(path)

    def _set_state(self, state):
        self.anim_state, self.anim_t0 = state, time.monotonic()
        if state == "idle":
            self.anim.stop()
            self.tray.setIcon(app_icon())
        else:
            self.anim.start()

    def _animate_icon(self):
        self.tray.setIcon(render_icon(32, self.anim_state, time.monotonic() - self.anim_t0))

    def _chime(self, name):
        if self.settings["sounds"] and IS_WINDOWS:
            import winsound

            winsound.PlaySound(os.path.join(ASSETS, f"{name}.wav"),
                               winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)

    def shutdown(self):
        if self.hotkeys:
            self.hotkeys.quit()
        self.tray.hide()


def main():
    if IS_WINDOWS:
        import ctypes

        from . import win32

        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # sharp popup on high-DPI screens
        if not win32.single_instance():
            from PySide6.QtNetwork import QLocalSocket

            s = QLocalSocket()  # already running: treat launching again as a toggle
            s.connectToServer(server_name())
            if s.waitForConnected(1000):
                s.write(b"toggle")
                s.waitForBytesWritten(1000)
            return
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName("Dictate")
    app.setWindowIcon(app_icon())
    controller = Controller(load_settings())
    app.aboutToQuit.connect(controller.shutdown)
    log("ready")
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
