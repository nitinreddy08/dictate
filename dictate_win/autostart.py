"""Start Dictate when you sign in to Windows (a per-user Run entry: no admin rights needed)."""
import os
import sys

KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
NAME = "Dictate"


def command():
    """pythonw.exe (no console window) running the app."""
    exe = sys.executable
    pythonw = os.path.join(os.path.dirname(exe), "pythonw.exe")
    return f'"{pythonw if os.path.exists(pythonw) else exe}" -m dictate_win'


def is_enabled():
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, KEY) as k:
            winreg.QueryValueEx(k, NAME)
            return True
    except OSError:
        return False


def set_enabled(on):
    import winreg

    # CreateKeyEx also opens it: the Run key doesn't exist yet on a brand-new Windows profile
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, KEY, 0, winreg.KEY_SET_VALUE) as k:
        if on:
            winreg.SetValueEx(k, NAME, 0, winreg.REG_SZ, command())
        else:
            try:
                winreg.DeleteValue(k, NAME)
            except OSError:
                pass
