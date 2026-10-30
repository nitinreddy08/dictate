"""Your last few dictations, so a misplaced one can be copied again. Kept only where you choose (RAM on
Linux, your private app-data folder on Windows) and trimmed to the newest few on every write."""
import json
import os
import time

KEEP = 3


def remember(path, text, keep=KEEP):
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.readlines()[-(keep - 1):] if keep > 1 else []
    except OSError:
        lines = []
    lines.append(json.dumps({"time": time.time(), "text": text}) + "\n")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.writelines(lines)


def recent(path):
    """Newest first: [{"time": epoch seconds, "text": str}, ...]"""
    try:
        with open(path, encoding="utf-8") as f:
            return [json.loads(line) for line in reversed(f.readlines()) if line.strip()]
    except (OSError, ValueError):
        return []


def clear(path):
    try:
        os.unlink(path)
    except OSError:
        pass
