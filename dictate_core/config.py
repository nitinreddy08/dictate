"""Where Dictate keeps its settings, models and vocabulary on each operating system."""
import json
import os
import sys

APP = "Dictate"


def config_dir():
    """Settings and vocabulary: %APPDATA%\\Dictate on Windows, ~/.config/dictate elsewhere."""
    if sys.platform == "win32":
        return os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), APP)
    return os.path.join(os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config")), "dictate")


def data_dir():
    """Downloaded models: %LOCALAPPDATA%\\Dictate on Windows, ~/.local/share/dictate elsewhere."""
    if sys.platform == "win32":
        return os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), APP)
    return os.path.join(os.environ.get("XDG_DATA_HOME", os.path.expanduser("~/.local/share")), "dictate")


def vocabulary_file():
    return os.path.join(config_dir(), "vocabulary.txt")


def vocabulary(path=None):
    """Words and names Whisper should prefer (one per line in the vocabulary file), or None."""
    try:
        with open(path or vocabulary_file(), encoding="utf-8") as f:
            words = [w.strip() for w in f if w.strip() and not w.lstrip().startswith("#")]
    except OSError:
        return None
    return ", ".join(words) or None


VOCABULARY_TEMPLATE = """\
# Dictate vocabulary: names and words Whisper should prefer, one per line.
# Add the people, places and terms you dictate often, especially ones it gets wrong.
# Lines starting with # are ignored. Changes apply on your next dictation.
#
# Examples:
# Rahul
# Bengaluru
# pull request
"""

DEFAULTS = {
    "model": "small.en",   # a key of dictate_core.catalog.MODELS
    "hotkey": "Insert",     # starts/stops dictation; Enter also stops while listening
    "sounds": True,         # soft start/stop chimes
    "autostart": True,      # start with Windows
    "idle_unload_secs": 90,  # free the model's memory after this long unused
}


def settings_file():
    return os.path.join(config_dir(), "settings.json")


def load_settings():
    settings = dict(DEFAULTS)
    try:
        with open(settings_file(), encoding="utf-8") as f:
            settings.update(json.load(f))
    except (OSError, ValueError):
        pass
    return settings


def save_settings(settings):
    os.makedirs(config_dir(), exist_ok=True)
    tmp = settings_file() + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(settings, f, indent=2)
    os.replace(tmp, settings_file())
