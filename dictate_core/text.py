"""Turning Whisper's raw output into the text you meant: voice commands and silence guards."""
import re

# Tells Whisper what to expect. On recorded voice tests (tools/voice_test.py + tools/benchmark.py) this,
# with Whisper's silence trimming off, cut word errors by about a third.
STYLE = "Okay, here is my dictation. It's written in clear Indian English, with proper punctuation."

# Spoken commands -> text. Matched case-insensitively with any punctuation Whisper put around them.
COMMANDS = [
    (r"new paragraph", "\n\n"),
    (r"new line|next line", "\n"),
    (r"full stop", "."),
    (r"comma", ","),
    (r"question mark", "?"),
    (r"exclamation (?:mark|point)", "!"),
    (r"colon", ":"),
    (r"semicolon", ";"),
]

# What Whisper writes when it hears only silence or room noise
SILENCE_PHRASES = ("thank you", "thanks for watching", "you")


def apply_commands(text):
    for spoken, out in COMMANDS:
        if out.strip():  # punctuation: attach to the previous word
            text = re.sub(rf"[\s,.]*\b(?:{spoken})\b[.,!?]*", out, text, flags=re.I)
        else:  # line breaks: drop the surrounding spaces and Whisper's punctuation
            text = re.sub(rf"[\s,]*\b(?:{spoken})\b[.,!?]*\s*", out, text, flags=re.I)
    # Capitalise the first letter after a line break the commands created
    text = re.sub(r"(\n)([a-z])", lambda m: m.group(1) + m.group(2).upper(), text)
    return text.strip(" ")


def clean(text, peak):
    """Final text for a recording whose loudest sample was `peak` (0..1): commands applied, and
    Whisper's made-up phrases on near-silence dropped."""
    text = apply_commands(text)
    if peak < 0.02 and text.lower().strip(" .!") in SILENCE_PHRASES:
        return ""
    return text
