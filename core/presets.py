"""Per-streamer region presets (camera/game rectangles + fit modes)."""
import json

from .config import PRESETS_FILE


def _load() -> dict:
    if not PRESETS_FILE.exists():
        return {}
    try:
        return json.loads(PRESETS_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def get(channel: str) -> dict | None:
    return _load().get((channel or "").lower())


def all_presets() -> dict:
    return _load()


def put(channel: str, preset: dict) -> None:
    """Merge into the channel's entry (regions and the whisper prompt are saved separately)."""
    data = _load()
    key = (channel or "bilinmeyen").lower()
    data[key] = {**data.get(key, {}), **preset}
    PRESETS_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
