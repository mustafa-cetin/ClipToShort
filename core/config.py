"""Paths and user settings. config.json overrides the defaults below."""
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORK_DIR = ROOT / "work"
OUTPUT_DIR = ROOT / "output"
FONTS_DIR = ROOT / "assets" / "fonts"
TOOLS_DIR = ROOT / "tools"
CONFIG_FILE = ROOT / "config.json"
PRESETS_FILE = ROOT / "presets.json"

DEFAULT_STYLE = {
    "font": "Arial Black",
    "size": 72,
    "color": "#FFFFFF",
    "outline_color": "#000000",
    "outline": 6,
    "shadow": 2,
    "offset_y": 0,        # px relative to the seam at y=960 (negative = up)
    "uppercase": True,
    "max_words": 3,
    "max_chars": 22,
}

DEFAULTS = {
    "ytdlp": "yt-dlp",
    "ffmpeg": "ffmpeg",
    "ffprobe": "ffprobe",
    "whisper_cli": "",     # empty → auto-detect under tools/whisper
    "whisper_model": "",   # empty → auto-detect under tools/whisper/models
    "whisper_language": "tr",
    "whisper_vad": True,      # Silero VAD: skip non-speech (stops invented text on music)
    "whisper_dtw": True,      # DTW token timestamps: accurate word timing
    "whisper_filter": True,   # highpass + dynaudnorm before whisper
    "max_source_seconds": 4 * 3600,
    "style": DEFAULT_STYLE,
}


def load() -> dict:
    cfg = json.loads(json.dumps(DEFAULTS))
    if CONFIG_FILE.exists():
        try:
            user = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            user = {}
        style = {**cfg["style"], **user.get("style", {})}
        cfg.update(user)
        cfg["style"] = style
    return cfg


def save_style(style: dict) -> None:
    user = {}
    if CONFIG_FILE.exists():
        try:
            user = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            user = {}
    user["style"] = {k: style[k] for k in DEFAULT_STYLE if k in style}
    CONFIG_FILE.write_text(json.dumps(user, indent=2, ensure_ascii=False), encoding="utf-8")


MODEL_PREFS = ["ggml-large-v3.bin", "ggml-large-v3-q5_0.bin", "ggml-large-v3-turbo.bin",
               "ggml-large-v3-turbo-q5_0.bin", "ggml-medium.bin", "ggml-small.bin", "ggml-base.bin"]
MODEL_LABELS = {  # file name → i18n message key
    "ggml-large-v3.bin": "model_large_v3",
    "ggml-large-v3-turbo.bin": "model_large_v3_turbo",
}


def model_label(model: Path) -> str:
    from .i18n import t
    key = MODEL_LABELS.get(model.name)
    return t(key) if key else model.stem.replace("ggml-", "")


def whisper_info(cfg: dict) -> dict:
    """whisper-cli executables (CUDA first), installed models, chosen model, VAD model."""
    exes: list[Path] = []
    if cfg.get("whisper_cli"):
        exes.append(Path(cfg["whisper_cli"]))
    wdir = TOOLS_DIR / "whisper"
    for sub in ("cuda", "cpu"):
        exes += sorted((wdir / sub).rglob("whisper-cli.exe"))
    on_path = shutil.which("whisper-cli")
    if on_path:
        exes.append(Path(on_path))
    exes = [e for e in exes if e.exists()]

    mdir = wdir / "models"
    installed = sorted((p for p in mdir.glob("ggml-*.bin") if "silero" not in p.name),
                       key=lambda p: MODEL_PREFS.index(p.name) if p.name in MODEL_PREFS else 99) \
        if mdir.exists() else []

    model = None
    want = cfg.get("whisper_model") or ""
    if want:
        cand = Path(want)
        if not cand.is_absolute():
            cand = mdir / want
        if cand.exists() and "silero" not in cand.name:
            model = cand
    if model is None and installed:
        model = installed[0]

    vads = sorted(mdir.glob("ggml-silero-*.bin")) if mdir.exists() else []
    vad_exe = next((e.parent / "whisper-vad-speech-segments.exe" for e in exes
                    if (e.parent / "whisper-vad-speech-segments.exe").exists()), None)
    return {"exes": exes, "model": model, "models": installed,
            "vad_model": vads[-1] if vads and vad_exe else None, "vad_exe": vad_exe}


def find_whisper(cfg: dict) -> tuple[list[Path], Path | None]:
    info = whisper_info(cfg)
    return info["exes"], info["model"]


def ensure_dirs() -> None:
    for d in (WORK_DIR, OUTPUT_DIR, FONTS_DIR, TOOLS_DIR):
        d.mkdir(parents=True, exist_ok=True)
    # libass needs the font file; Arial Black ships with Windows.
    src = Path("C:/Windows/Fonts/ariblk.ttf")
    dst = FONTS_DIR / "ariblk.ttf"
    if src.exists() and not dst.exists():
        try:
            shutil.copy(src, dst)
        except OSError:
            pass
