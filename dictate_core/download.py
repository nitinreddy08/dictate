"""Downloading a model from Hugging Face: resumable, verified against the published SHA-256, and
installed atomically (a half-finished download is never mistaken for a working model)."""
import hashlib
import json
import os
import shutil
import urllib.request

API = "https://huggingface.co/api/models/{repo}/tree/main"
FILE = "https://huggingface.co/{repo}/resolve/main/{path}"
NEEDED = {"config.json", "model.bin", "tokenizer.json", "vocabulary.txt", "vocabulary.json",
          "preprocessor_config.json"}
CHUNK = 1 << 20


def _get(url, headers=None):
    req = urllib.request.Request(url, headers={"User-Agent": "dictate", **(headers or {})})
    return urllib.request.urlopen(req, timeout=30)


def files(repo):
    """[(path, size, sha256 or None)] for the files a model needs."""
    with _get(API.format(repo=repo)) as r:
        tree = json.load(r)
    return [(f["path"], f.get("size", 0), (f.get("lfs") or {}).get("oid"))
            for f in tree if f.get("type") == "file" and f["path"] in NEEDED]


def is_installed(models_dir, key):
    d = os.path.join(models_dir, key)
    return os.path.isfile(os.path.join(d, "model.bin")) and os.path.isfile(os.path.join(d, ".complete"))


def download(repo, models_dir, key, progress=None):
    """Download `repo` into models_dir/key. `progress(file, done_bytes, total_bytes)` is called as data
    arrives (done/total cover all files). Partial files are resumed on the next try."""
    parts = files(repo)
    total = sum(size for _, size, _ in parts)
    staging = os.path.join(models_dir, f".{key}.partial")
    os.makedirs(staging, exist_ok=True)
    done = 0
    for path, size, sha in parts:
        dest = os.path.join(staging, path)
        have = os.path.getsize(dest) if os.path.exists(dest) else 0
        if have < size:
            headers = {"Range": f"bytes={have}-"} if have else {}
            with _get(FILE.format(repo=repo, path=path), headers) as r, open(dest, "ab" if have else "wb") as f:
                if have and r.status != 206:  # server ignored the resume request: start over
                    f.truncate(0)
                    have = 0
                while chunk := r.read(CHUNK):
                    f.write(chunk)
                    have += len(chunk)
                    if progress:
                        progress(path, done + have, total)
        done += size
        if progress:
            progress(path, done, total)
        if sha:
            if progress:
                progress(f"verifying {path}", done, total)
            if _sha256(dest) != sha:
                os.unlink(dest)
                raise IOError(f"{path} didn't match its published checksum (deleted; please try again)")
    open(os.path.join(staging, ".complete"), "w").close()
    final = os.path.join(models_dir, key)
    if os.path.exists(final):
        shutil.rmtree(final)
    os.replace(staging, final)
    return final


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(CHUNK):
            h.update(chunk)
    return h.hexdigest()


def remove(models_dir, key):
    shutil.rmtree(os.path.join(models_dir, key), ignore_errors=True)
