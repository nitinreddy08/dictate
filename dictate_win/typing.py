"""Typing the dictated text into whatever app you're in. Uses Windows' Unicode keyboard input, so any
character works (₹, é, —, emoji) on any keyboard layout, without touching your clipboard."""
import time

from . import win32 as w

VK_SHIFT, VK_RETURN = w.VK["Shift"], w.VK["Return"]


def _keystrokes(text):
    """One list of key events per character, so a character is never split between two sends."""
    for ch in text:
        if ch == "\n":  # Shift+Enter: a new line in chat apps too, instead of sending the message
            yield [w.key_input(VK_SHIFT), w.key_input(VK_RETURN),
                   w.key_input(VK_RETURN, flags=w.KEYEVENTF_KEYUP), w.key_input(VK_SHIFT, flags=w.KEYEVENTF_KEYUP)]
            continue
        data = ch.encode("utf-16-le")
        units = [int.from_bytes(data[i:i + 2], "little") for i in range(0, len(data), 2)]
        # Emoji and other characters outside the BMP are two UTF-16 units: press both, then release both,
        # which is how Windows apps expect a surrogate pair
        yield ([w.key_input(scan=u, flags=w.KEYEVENTF_UNICODE) for u in units] +
               [w.key_input(scan=u, flags=w.KEYEVENTF_UNICODE | w.KEYEVENTF_KEYUP) for u in units])


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
    """Send `text` as keystrokes to the focused window. True if Windows accepted every event."""
    sent = total = 0
    group = []
    for keys in _keystrokes(text):
        group += keys
        if len(group) >= batch:
            sent, total = sent + w.send(group), total + len(group)
            group = []
            time.sleep(0.005)  # let slower apps keep up with long dictations
    if group:
        sent, total = sent + w.send(group), total + len(group)
    return sent == total
