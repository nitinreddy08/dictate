"""The English speech models you can choose from, with honest pros and cons.

Speed and memory were measured on a 4-core Intel i5-1135G7 laptop transcribing 15 seconds of speech
(tools/benchmark.py); "estimate" marks numbers that weren't measured. distil-small.en is left out: in
testing it dropped half a sentence.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class ModelInfo:
    key: str           # the name faster-whisper knows it by
    repo: str          # Hugging Face repository with the CTranslate2 files
    download_mb: int
    memory_mb: int     # while dictating
    seconds: float     # to transcribe 15 s of speech on a 4-core laptop CPU
    accuracy: int      # 1..5
    measured: bool
    best_for: str
    pros: str
    cons: str


MODELS = {m.key: m for m in [
    ModelInfo("tiny.en", "Systran/faster-whisper-tiny.en", 78, 300, 0.4, 2, True,
              "Old or low-memory PCs",
              "Smallest and fastest; fine for short, clear notes",
              "Misses more words, especially names and accents"),
    ModelInfo("base.en", "Systran/faster-whisper-base.en", 148, 410, 0.65, 3, True,
              "Laptops with 4 GB of memory",
              "Quick, light, decent on clear speech",
              "Stumbles on place names and fast speech"),
    ModelInfo("small.en", "Systran/faster-whisper-small.en", 486, 850, 1.7, 4, True,
              "Most PCs (8 GB of memory or more)",
              "Accurate with accents and names; the best balance",
              "Needs ~850 MB while dictating (freed after 90 s)"),
    ModelInfo("medium.en", "Systran/faster-whisper-medium.en", 1530, 2000, 5.0, 5, False,
              "Fast desktops with 16 GB or more",
              "Fewer mistakes on hard audio and jargon",
              "3x slower than small.en; large download"),
    ModelInfo("large-v3-turbo", "mobiuslabsgmbh/faster-whisper-large-v3-turbo", 1622, 2300, 6.0, 5, False,
              "Powerful PCs; the most accurate",
              "Whisper's best accuracy, robust to noise and accents",
              "Slow on a laptop processor; large download"),
]}

DEFAULT = "small.en"


def recommend(memory_gb, cores):
    """The model that fits this PC best."""
    # "8 GB" laptops often report under 7 GB usable (graphics memory is carved out), and small.en runs
    # well on them; bigger models stay slow on any processor without a GPU, so small.en is the ceiling
    if memory_gb >= 5.5 and cores >= 4:
        return "small.en"
    if memory_gb >= 3.5:
        return "base.en"
    return "tiny.en"
