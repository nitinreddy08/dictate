"""Typing the dictated text into whatever app you're in. Uses Windows' Unicode keyboard input, so any
character works (₹, é, —, emoji) on any keyboard layout, without touching your clipboard."""
import time

from . import win32 as w

VK_SHIFT, VK_RETURN = w.VK["Shift"], w.VK["Return"]


def _events(text):
    for ch in text:
        if ch == "\n":  # Shift+Enter: a new line in chat apps too, instead of sending the message
            yield w.key_input(VK_SHIFT)
            yield w.key_input(VK_RETURN)
            yield w.key_input(VK_RETURN, flags=w.KEYEVENTF_KEYUP)
            yield w.key_input(VK_SHIFT, flags=w.KEYEVENTF_KEYUP)
            continue
        data = ch.encode("utf-16-le")
        for i in range(0, len(data), 2):  # characters outside the BMP (emoji) are two UTF-16 units
            unit = int.from_bytes(data[i:i + 2], "little")
            yield w.key_input(scan=unit, flags=w.KEYEVENTF_UNICODE)
            yield w.key_input(scan=unit, flags=w.KEYEVENTF_UNICODE | w.KEYEVENTF_KEYUP)


def where_to():
    """"type" normally; "clipboard" when nothing can take typed text (desktop, taskbar) or Windows would
    block it (an app running as administrator)."""
    cls = w.foreground_class()
    if not cls or cls in w.SHELL_CLASSES:
        return "clipboard"
    if w.foreground_is_admin_only():
        return "clipboard"
    return "type"


def type_text(text, batch=64):
    """Send `text` as keystrokes to the focused window. Returns how many characters' events went through."""
    events = list(_events(text))
    sent = 0
    for i in range(0, len(events), batch):
        sent += w.send(events[i:i + batch])
        time.sleep(0.005)  # let slower apps keep up with long dictations
    return sent == len(events)
