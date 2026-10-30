#!/usr/bin/env python3
"""Record yourself reading a few test sentences, once per microphone, for tools/benchmark.py.

Recordings go to $XDG_RUNTIME_DIR/dictate-voicetest (RAM, cleared at logout).
"""
import json
import os
import re
import subprocess
import sys
import time

OUT = os.path.join(os.environ.get("XDG_RUNTIME_DIR", "/tmp"), "dictate-voicetest")

SENTENCES = [
    "Hey Rahul, can you send me the updated invoice by Thursday evening?",
    "The meeting with the Bengaluru team has moved to 3:30 tomorrow afternoon.",
    "Please review the pull request and merge it once all the tests pass.",
    "I paid 2,500 rupees for the train ticket to Hyderabad last week.",
    "Let's schedule a quick call with Priya and Anand to plan the product launch.",
    "Honestly, the new design looks great, but the loading time on mobile still needs some work.",
]


def sources():
    """{node.name: description} for every microphone PipeWire knows about."""
    status = subprocess.run(["wpctl", "status"], capture_output=True, text=True).stdout
    found = {}
    for sid in re.findall(r"(\d+)\.\s", status.split("Sources:")[1].split("Filters:")[0]):
        info = subprocess.run(["wpctl", "inspect", sid], capture_output=True, text=True).stdout
        name = re.search(r'node\.name = "([^"]*)"', info)
        desc = re.search(r'node\.description = "([^"]*)"', info)
        if name:
            found[name.group(1)] = desc.group(1) if desc else name.group(1)
    default = subprocess.run(["wpctl", "inspect", "@DEFAULT_AUDIO_SOURCE@"], capture_output=True, text=True).stdout
    m = re.search(r'node\.name = "([^"]*)"', default)
    if m and m.group(1) not in found:
        d = re.search(r'node\.description = "([^"]*)"', default)
        found[m.group(1)] = d.group(1) if d else m.group(1)
    return found


def pick_mics():
    mics = sources()
    chosen = []
    bt = next((n for n in mics if n.startswith("bluez_input")), None)
    if bt:
        chosen.append(("earbuds", bt, mics[bt]))
    builtin = next((n for n, d in mics.items() if "Digital Microphone" in d), None) or \
        next((n for n in mics if n.startswith("alsa_input")), None)
    if builtin:
        chosen.append(("laptop", builtin, mics[builtin]))
    return chosen


def record(target, path):
    proc = subprocess.Popen(["pw-record", "--target", target, "--rate", "16000", "--channels", "1",
                             "--format", "s16", path], stderr=subprocess.DEVNULL)
    time.sleep(0.4)  # let the mic wake up (Bluetooth needs a moment)
    input("   \033[31m●\033[0m Recording — read the sentence, then press Enter ")
    time.sleep(0.3)
    proc.terminate()
    proc.wait()


def main():
    mics = pick_mics()
    if not mics:
        sys.exit("No microphones found.")
    print("\nDictate voice test — read each sentence naturally, the way you'd normally dictate.\n")
    for label, target, desc in mics:
        folder = os.path.join(OUT, label)
        os.makedirs(folder, exist_ok=True)
        print(f"\033[1mRound: {desc}\033[0m" + ("  (put your earbuds in)" if label == "earbuds" else
                                                  "  (take the earbuds out or switch them off for this round)"))
        input("Press Enter when ready… ")
        for i, sentence in enumerate(SENTENCES, 1):
            print(f"\n  {i}/{len(SENTENCES)}  \033[1m{sentence}\033[0m")
            input("   Press Enter to start ")
            record(target, os.path.join(folder, f"{i:02d}.wav"))
        print()
    with open(os.path.join(OUT, "sentences.json"), "w") as f:
        json.dump({"sentences": SENTENCES, "mics": {l: d for l, _, d in mics}}, f)
    print("All done — thank you! Tell Claude the recordings are ready.\n")


if __name__ == "__main__":
    main()
