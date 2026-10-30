#!/usr/bin/env python3
"""Compare Whisper models on the recordings from tools/voice_test.py: accuracy, speed and memory.

  .venv/bin/python tools/benchmark.py small.en medium.en large-v3-turbo distil-large-v3
"""
import gc
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["DICTATE_SOUNDS"] = "0"  # recordings have no chime to skip
import dictate  # noqa: E402

DATA = os.path.join(os.environ.get("XDG_RUNTIME_DIR", "/tmp"), "dictate-voicetest")


def words(text):
    text = text.lower().replace("’", "'")
    text = re.sub(r"(?<=\d)[,:.](?=\d)", "", text)  # 2,500 -> 2500, 3:30 / 3.30 -> 330
    text = re.sub(r"\b(?:rs\.?|₹)\s*(\d+)", r"\1 rupees", text)  # Rs. 2500 / ₹2500 -> 2500 rupees
    return re.findall(r"[a-z0-9']+", text)


def wer(ref, hyp):
    """Word error rate: (substitutions + insertions + deletions) / reference words."""
    r, h = words(ref), words(hyp)
    d = list(range(len(h) + 1))
    for i in range(1, len(r) + 1):
        prev, d[0] = d[0], i
        for j in range(1, len(h) + 1):
            cur = min(d[j] + 1, d[j - 1] + 1, prev + (r[i - 1] != h[j - 1]))
            prev, d[j] = d[j], cur
    return d[len(h)] / max(len(r), 1)


def rss_mb():
    with open("/proc/self/status") as f:
        return int(re.search(r"VmRSS:\s+(\d+)", f.read()).group(1)) // 1024


class Quiet:
    def send(self, *a): pass
    def close(self): pass


def main(models):
    meta = json.load(open(os.path.join(DATA, "sentences.json")))
    refs = meta["sentences"]
    mics = [m for m in meta["mics"] if os.path.isdir(os.path.join(DATA, m))]
    audio = {(m, i): dictate.load_wav(os.path.join(DATA, m, f"{i + 1:02d}.wav"))
             for m in mics for i in range(len(refs)) if os.path.exists(os.path.join(DATA, m, f"{i + 1:02d}.wav"))}
    results = {}
    for name in models:
        dictate.MODEL = name
        model = dictate.Model(Quiet())
        t = time.time()
        model.get()
        load = time.time() - t
        model.transcribe(audio[next(iter(audio))][:16000])  # warm-up
        rows = []
        for (mic, i), a in audio.items():
            t = time.time()
            hyp = model.transcribe(a)
            rows.append({"mic": mic, "n": i + 1, "secs": time.time() - t, "audio": len(a) / 16000,
                         "wer": wer(refs[i], hyp), "text": hyp})
        results[name] = {"load": load, "rss": rss_mb(), "rows": rows}
        print(f"\n== {name}: loaded in {load:.1f}s, process RAM {rss_mb()} MB")
        for mic in mics:
            mine = [r for r in rows if r["mic"] == mic]
            print(f"   {mic:8} WER {100 * sum(r['wer'] for r in mine) / len(mine):5.1f}%   "
                  f"avg {sum(r['secs'] for r in mine) / len(mine):.2f}s per sentence")
            for r in mine:
                if r["wer"] > 0:
                    print(f"      {r['n']}: {r['text']}")
        model.model = None
        del model
        gc.collect()
    json.dump(results, open(os.path.join(DATA, "results.json"), "w"), indent=1)


if __name__ == "__main__":
    main(sys.argv[1:] or ["small.en"])
