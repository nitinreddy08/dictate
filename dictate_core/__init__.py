"""Platform-independent parts of Dictate: speech-to-text, text clean-up, history and the model catalog.

The Linux service (dictate.py) and the Windows app (dictate_win) both build on this package.
"""

__version__ = "0.2.0"
RATE = 16000  # Hz, mono: what Whisper expects
