"""Global shortcut (Insert by default) and Enter-to-finish, on a background thread with its own message
loop. Enter is only intercepted while you're dictating; the rest of the time it's untouched."""
import ctypes
import threading

from . import win32 as w

_HOTKEY_ID = 1
_SET_LISTENING = w.WM_APP + 1


def parse(combo):
    """"Ctrl+Alt+Space" -> (modifiers, virtual key). Raises ValueError for unknown keys."""
    mods, vk = w.MOD_NOREPEAT, None
    for part in (p.strip() for p in combo.split("+")):
        low = part.lower()
        if low in ("ctrl", "control"):
            mods |= w.MOD_CONTROL
        elif low == "alt":
            mods |= w.MOD_ALT
        elif low == "shift":
            mods |= w.MOD_SHIFT
        elif low in ("win", "super"):
            mods |= w.MOD_WIN
        elif part in w.VK:
            vk = w.VK[part]
        elif len(part) == 1 and part.isalnum():
            vk = ord(part.upper())
        else:
            raise ValueError(f"unknown key: {part}")
    if vk is None:
        raise ValueError(f"no key in shortcut: {combo}")
    return mods, vk


class Hotkeys(threading.Thread):
    """Calls on_toggle() when the shortcut is pressed and on_stop() when Enter is pressed while listening.
    Callbacks run on this thread: keep them quick (e.g. emit a Qt signal)."""

    def __init__(self, combo, on_toggle, on_stop):
        super().__init__(daemon=True, name="hotkeys")
        self.combo, self.on_toggle, self.on_stop = combo, on_toggle, on_stop
        self.thread_id = None
        self.registered = threading.Event()
        self.error = None
        self._hook = None
        self._hook_proc = w.HOOKPROC(self._keyboard_hook)  # keep a reference: Windows calls it later

    def run(self):
        self.thread_id = w.kernel32.GetCurrentThreadId()
        mods, vk = parse(self.combo)
        if not w.user32.RegisterHotKey(None, _HOTKEY_ID, mods, vk):
            self.error = f"{self.combo} is already used by another app"
        self.registered.set()
        msg = w.MSG()
        while w.user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == w.WM_HOTKEY and msg.wParam == _HOTKEY_ID:
                self.on_toggle()
            elif msg.message == _SET_LISTENING:
                self._set_hook(bool(msg.wParam))
        self._set_hook(False)
        w.user32.UnregisterHotKey(None, _HOTKEY_ID)

    def set_listening(self, listening):
        """Start/stop intercepting Enter (thread-safe)."""
        if self.thread_id:
            w.user32.PostThreadMessageW(self.thread_id, _SET_LISTENING, int(listening), 0)

    def quit(self):
        if self.thread_id:
            w.user32.PostThreadMessageW(self.thread_id, w.WM_QUIT, 0, 0)

    def _set_hook(self, on):
        if on and not self._hook:
            self._hook = w.user32.SetWindowsHookExW(w.WH_KEYBOARD_LL, self._hook_proc,
                                                    w.kernel32.GetModuleHandleW(None), 0)
        elif not on and self._hook:
            w.user32.UnhookWindowsHookEx(self._hook)
            self._hook = None

    def _keyboard_hook(self, code, wparam, lparam):
        if code == 0:
            k = ctypes.cast(lparam, ctypes.POINTER(w.KBDLLHOOKSTRUCT)).contents
            if k.vkCode == w.VK["Return"] and not k.flags & w.LLKHF_INJECTED:
                if wparam in (w.WM_KEYDOWN, w.WM_SYSKEYDOWN):
                    self.on_stop()
                return 1  # swallow Enter (down and up) so the app doesn't also get it
        return w.user32.CallNextHookEx(self._hook, code, wparam, lparam)
