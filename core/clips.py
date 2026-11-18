"""A clip = one work directory with source.mp4 + clip.json holding all editor state."""
import json
import re
import shutil
import threading
import time
import uuid
from pathlib import Path

from .config import WORK_DIR

_ID = re.compile(r"^[a-f0-9]{12}$")
_lock = threading.RLock()


def new_id() -> str:
    return uuid.uuid4().hex[:12]


def clip_dir(clip_id: str) -> Path:
    if not _ID.match(clip_id or ""):
        raise ValueError("Geçersiz klip id")
    return WORK_DIR / clip_id


def load(clip_id: str) -> dict | None:
    f = clip_dir(clip_id) / "clip.json"
    if not f.exists():
        return None
    return json.loads(f.read_text(encoding="utf-8"))


def save(clip: dict) -> None:
    d = clip_dir(clip["id"])
    d.mkdir(parents=True, exist_ok=True)
    clip["updated"] = time.time()
    with _lock:
        tmp = d / "clip.json.tmp"
        tmp.write_text(json.dumps(clip, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(d / "clip.json")


def mutate(clip_id: str, fn) -> dict:
    """Atomic read-modify-write of clip.json (jobs and UI autosave both write it)."""
    with _lock:
        clip = load(clip_id)
        if clip is None:
            raise KeyError(clip_id)
        fn(clip)
        save(clip)
        return clip


def update(clip_id: str, **fields) -> dict:
    clip = load(clip_id)
    if clip is None:
        raise KeyError(clip_id)
    clip.update(fields)
    save(clip)
    return clip


def new_cut_id() -> str:
    return uuid.uuid4().hex[:8]


def ensure_cuts(clip: dict) -> dict:
    """Older single-clip records → one cut spanning the whole source."""
    if clip.get("cuts") is None and clip.get("video"):
        clip["cuts"] = [{"id": new_cut_id(), "name": "Klip 1", "start": 0.0,
                         "end": round(clip["video"]["duration"], 2),
                         "regions": None, "modes": None, "output": clip.get("output")}]
    return clip


def find_cut(clip: dict, cut_id: str) -> dict | None:
    return next((c for c in clip.get("cuts") or [] if c.get("id") == cut_id), None)


def list_all() -> list[dict]:
    out = []
    if WORK_DIR.exists():
        for f in WORK_DIR.glob("*/clip.json"):
            try:
                c = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            item = {k: c.get(k) for k in ("id", "channel", "title", "start", "end", "status", "created")}
            cuts = c.get("cuts") or []
            item["cuts"] = len(cuts)
            item["rendered"] = sum(1 for x in cuts if x.get("output")) or (1 if c.get("output") and not cuts else 0)
            out.append(item)
    out.sort(key=lambda c: c.get("created") or 0, reverse=True)
    return out


def delete(clip_id: str) -> None:
    shutil.rmtree(clip_dir(clip_id), ignore_errors=True)
