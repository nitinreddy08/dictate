"""Windows-only checks that need a real desktop (run on GitHub's Windows machines by CI):

  1. Unicode typing reaches a real text box (₹, é, —, ✓, emoji, Shift+Enter new lines)
  2. The global shortcut registers and fires
  3. The tray icon can be shown
  4. Every popup state renders (screenshots saved to the folder given as the first argument)

Exits non-zero if anything fails, printing what did.
"""
import os
import sys
import time

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QPlainTextEdit, QSystemTrayIcon

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dictate_win import typing, win32  # noqa: E402
from dictate_win.hotkeys import Hotkeys  # noqa: E402
from dictate_win.ui import Popup, app_icon  # noqa: E402

OUT = sys.argv[1] if len(sys.argv) > 1 else "screenshots"
os.makedirs(OUT, exist_ok=True)
TEXT = "Héllo ₹2,500 — naïve café ✓ 🎉\nSecond line"
results = {}
app = QApplication(sys.argv)


def wait(ms):
    end = time.monotonic() + ms / 1000
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.01)


def screenshot(name):
    app.primaryScreen().grabWindow(0).save(os.path.join(OUT, f"{name}.png"))


# 1. typing into a real window
box = QPlainTextEdit()
box.setWindowTitle("Dictate typing test")
box.resize(600, 200)
box.show()
box.raise_()
box.activateWindow()
win32.user32.SetForegroundWindow(int(box.winId()))
box.setFocus()
wait(800)
results["foreground is the test window"] = win32.foreground_class() != "" and typing.where_to() == "type"
typing.type_text(TEXT)
wait(800)
got = box.toPlainText()
results["typed text arrived exactly"] = got == TEXT
if got != TEXT:
    print(f"expected {TEXT!r}\n     got {got!r}")
screenshot("typing")
box.close()

# 2. the global shortcut
fired = []
hk = Hotkeys("Insert", lambda: fired.append("toggle"), lambda: fired.append("stop"))
hk.start()
hk.registered.wait(2)
results["Insert shortcut registered"] = hk.error is None
win32.send([win32.key_input(win32.VK["Insert"]), win32.key_input(win32.VK["Insert"], flags=win32.KEYEVENTF_KEYUP)])
wait(500)
results["Insert shortcut fires"] = fired == ["toggle"]
hk.quit()

# 3. tray
tray = QSystemTrayIcon(app_icon())
tray.show()
wait(300)
results["tray available"] = QSystemTrayIcon.isSystemTrayAvailable()

# 4. popup states
pop = Popup()
for name, cmd in [("listening", "listening Microphone Array (Realtek)"), ("transcribing", "transcribing"),
                  ("done", "done Hey Rahul, can you send me the updated invoice by Thursday evening? "
                           "I paid ₹2,500 for the ticket."),
                  ("nofield", "nofield Hello"), ("error", "error Your microphone is muted|Unmute it in Settings")]:
    pop.command(cmd)
    if name == "listening":
        for i in range(40):
            pop.command(f"level {0.2 + 0.6 * ((i * 7) % 10) / 10:.2f}")
            wait(40)
    wait(600)
    screenshot(f"popup_{name}")
results["popup shown"] = pop.isVisible()

failed = [k for k, v in results.items() if not v]
for k, v in results.items():
    print(f"{'PASS' if v else 'FAIL'}  {k}")
QTimer.singleShot(0, app.quit)
app.exec()
sys.exit(1 if failed else 0)
