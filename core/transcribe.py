"""Offline speech-to-text with whisper.cpp (whisper-cli).

Quality measures (see README):
  * DTW token timestamps (-dtw, needs -nfa) → word timings that follow the actual speech.
    Whisper's default per-token offsets just spread a segment's duration over its words.
  * Silero VAD (--vad) + -sns + -mc 0 + hallucination filter → no invented text over
    music/game audio and no repetition loops.
  * Optional initial prompt (per streamer) → better names/slang.
"""
import json
import os
import re
import threading
import unicodedata
from pathlib import Path

from . import clips, config
from .jobs import JobError, run_process, run_simple

_PROGRESS = re.compile(r"progress\s*=\s*(\d+)%")
_END_PUNCT = (".", "!", "?", "…")
_PUNCT_ONLY = re.compile(r"^[\s.,!?…;:\-–—\"'«»()]+$")

# Phrases Whisper is known to invent on silence/music (normalized: lowercase, no punctuation).
HALLUCINATIONS = [
    "altyazı mk", "altyazı m k", "altyazılar mk", "altyazı", "izlediğiniz için teşekkürler",
    "izlediğiniz için teşekkür ederim", "izlediğiniz için teşekkür ederiz", "abone olmayı unutmayın",
    "kanalıma abone olmayı unutmayın", "beğenmeyi ve abone olmayı unutmayın",
    "bir sonraki videoda görüşmek üzere", "videoyu beğenmeyi unutmayın",
    "thanks for watching", "thank you for watching", "thank you", "please subscribe",
    "subtitles by the amaraorg community", "subtitles by", "music", "müzik",
]

# -dtw presets by model file name (most specific first).
_DTW_PRESETS = [("large-v3-turbo", "large.v3.turbo"), ("large-v3", "large.v3"),
                ("large-v2", "large.v2"), ("large-v1", "large.v1"), ("medium.en", "medium.en"),
                ("medium", "medium"), ("small.en", "small.en"), ("small", "small"),
                ("base.en", "base.en"), ("base", "base"), ("tiny.en", "tiny.en"), ("tiny", "tiny")]


def dtw_preset(model_file: str) -> str | None:
    name = Path(model_file).name.lower()
    for key, preset in _DTW_PRESETS:
        if f"ggml-{key}" in name:
            return preset
    return None


def normalize(text: str) -> str:
    t = unicodedata.normalize("NFC", text).replace("İ", "i").replace("I", "ı").lower()
    t = re.sub(r"[^\w\s]", " ", t)
    return " ".join(t.split())


def is_hallucination(text: str) -> bool:
    n = normalize(text)
    if not n:
        return True
    return any(n == h or (n.startswith(h) and len(h) >= 0.7 * len(n)) for h in HALLUCINATIONS)


# ---------------------------------------------------------------------------
# whisper JSON → words
# ---------------------------------------------------------------------------
def _is_special(tok_text: str) -> bool:
    return tok_text.startswith("[_") and tok_text.endswith("]")


def words_from_tokens(data: dict, min_conf: float = 0.35) -> list[dict]:
    """whisper-cli -ojf (+ -dtw) output → [{text, start, end, p}] in seconds.

    Word = a token starting with a space plus following tokens without one. Timing uses the
    token's DTW time (t_dtw, centiseconds) when available, else its offsets (ms). Word text
    comes from the segment text when the word counts match (tokens may split UTF-8 bytes)."""
    words: list[dict] = []
    for seg in data.get("transcription", []):
        seg_text = (seg.get("text") or "").strip()
        seg_off = seg.get("offsets") or {}
        seg_end = seg_off.get("to", 0) / 1000
        if not seg_text or is_hallucination(seg_text):
            continue
        groups: list[dict] = []  # {text, t, p:[...]}
        last_t = None
        for tok in seg.get("tokens") or []:
            tt = tok.get("text") or ""
            if _is_special(tt):
                continue
            t = tok.get("t_dtw", -1)
            t = t / 100 if t is not None and t >= 0 else (tok.get("offsets") or {}).get("from", 0) / 1000
            last_t = t
            if _PUNCT_ONLY.match(tt) and groups:
                groups[-1]["text"] += tt.strip()
                continue
            if tt.startswith(" ") or not groups:
                groups.append({"text": tt.strip(), "t": t, "p": [tok.get("p", 1.0)]})
            else:
                groups[-1]["text"] += tt
                groups[-1]["p"].append(tok.get("p", 1.0))
        groups = [g for g in groups if g["text"]]
        if not groups:
            continue
        seg_words = seg_text.split()
        if len(seg_words) == len(groups):
            for g, w in zip(groups, seg_words):
                g["text"] = w
        # Drop short, low-confidence segments (typical noise hallucinations).
        mean_p = sum(sum(g["p"]) / len(g["p"]) for g in groups) / len(groups)
        if len(groups) <= 2 and mean_p < min_conf:
            continue
        tail = max(last_t or groups[-1]["t"], groups[-1]["t"]) + 0.25
        for i, g in enumerate(groups):
            nxt = groups[i + 1]["t"] if i + 1 < len(groups) else min(tail, max(seg_end, g["t"] + 0.1))
            start = g["t"]
            end = min(max(nxt, start + 0.08), start + 1.2)
            words.append({"text": g["text"], "start": round(start, 3), "end": round(end, 3),
                          "p": round(sum(g["p"]) / len(g["p"]), 3)})
    words.sort(key=lambda w: w["start"])
    return collapse_repeats(words)


def collapse_repeats(words: list[dict], max_run: int = 3) -> list[dict]:
    """Remove Whisper repetition loops: an n-gram (n=1..4) repeated more than max_run times."""
    out = list(words)
    for n in (1, 2, 3, 4):  # shortest first, so "x x x x" collapses to one "x"
        i, res = 0, []
        while i < len(out):
            gram = [normalize(w["text"]) for w in out[i:i + n]]
            j = i + n
            runs = 1
            while j + n <= len(out) and [normalize(w["text"]) for w in out[j:j + n]] == gram:
                runs += 1
                j += n
            if len(gram) == n and runs > max_run:
                res.extend(out[i:i + n])  # keep one occurrence
                i = j
            else:
                res.append(out[i])
                i += 1
        out = res
    return out


def parse_whisper_json(data: dict) -> list[dict]:
    """Legacy: whisper-cli -ml 1 -sow -oj output → [{text, start, end}] (seconds)."""
    words: list[dict] = []
    for seg in data.get("transcription", []):
        text = (seg.get("text") or "").strip()
        off = seg.get("offsets") or {}
        if not text:
            continue
        start, end = off.get("from", 0) / 1000, off.get("to", 0) / 1000
        if _PUNCT_ONLY.match(text) and words:
            words[-1]["text"] += text
            words[-1]["end"] = max(words[-1]["end"], end)
            continue
        if text.startswith("[") and text.endswith("]"):
            continue  # [_BEG_], [Müzik] etc.
        words.append({"text": text, "start": start, "end": max(end, start + 0.05)})
    return words


# ---------------------------------------------------------------------------
# words → captions
# ---------------------------------------------------------------------------
def words_to_segments(words: list[dict], max_words: int = 3, max_chars: int = 22,
                      max_gap: float = 0.6, bridge: float = 0.3,
                      min_dur: float = 0.25) -> list[dict]:
    """Group words into TikTok-style short captions."""
    segs: list[dict] = []
    cur: list[dict] = []

    def flush():
        if cur:
            segs.append({"start": round(cur[0]["start"], 2), "end": round(cur[-1]["end"], 2),
                         "text": " ".join(w["text"] for w in cur)})
            cur.clear()

    for w in words:
        if cur:
            joined = len(" ".join(x["text"] for x in cur)) + 1 + len(w["text"])
            if (len(cur) >= max_words or joined > max_chars
                    or w["start"] - cur[-1]["end"] > max_gap
                    or cur[-1]["text"].endswith(_END_PUNCT)):
                flush()
        cur.append(w)
    flush()

    # Give every caption a minimum duration, pushing the following ones later if needed.
    for i, s in enumerate(segs):
        if i and s["start"] < segs[i - 1]["end"]:
            s["start"] = segs[i - 1]["end"]
        s["end"] = round(max(s["end"], s["start"] + min_dur), 2)

    # Close tiny gaps so captions don't flicker between segments.
    for a, b in zip(segs, segs[1:]):
        if 0 < b["start"] - a["end"] <= bridge:
            a["end"] = b["start"]
        if a["end"] > b["start"]:
            a["end"] = b["start"]
    return segs


def merge_segments(existing: list[dict], new: list[dict], start: float, end: float) -> list[dict]:
    """Replace captions inside [start, end] with `new`; keep everything outside."""
    kept = [s for s in existing or [] if s["end"] <= start or s["start"] >= end]
    return sorted(kept + new, key=lambda s: s["start"])


# ---------------------------------------------------------------------------
# VAD: we run Silero separately (whisper-vad-speech-segments) instead of whisper-cli --vad,
# because with --vad whisper.cpp leaves DTW token times on the VAD-compressed timeline.
# Only the speech chunks are fed to whisper (concatenated with short pauses) — a long
# non-speech/silent stretch at the start of a 30 s window makes large-v3 output its
# "Altyazı M.K." hallucination instead of the speech. Times are mapped back ourselves.
# ---------------------------------------------------------------------------
_VAD_LINE = re.compile(r"start\s*=\s*([\d.]+)\s*,\s*end\s*=\s*([\d.]+)")


def parse_vad_output(text: str) -> list[tuple[float, float]]:
    """'Speech segment 0: start = 208.00, end = 281.00' (centiseconds) → [(2.08, 2.81)]."""
    return [(float(a) / 100, float(b) / 100) for a, b in _VAD_LINE.findall(text)]


def merge_intervals(iv: list[tuple[float, float]], total: float, pad: float = 0.2,
                    gap: float = 0.3) -> list[tuple[float, float]]:
    out: list[list[float]] = []
    for a, b in sorted(iv):
        a, b = max(0.0, a - pad), min(total, b + pad)
        if out and a - out[-1][1] <= gap:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return [(round(a, 3), round(b, 3)) for a, b in out]


SPEECH_GAP = 0.4  # silence inserted between concatenated speech chunks (s)


def build_speech_track(path, intervals: list[tuple[float, float]],
                       gap: float = SPEECH_GAP) -> list[tuple[float, float, float]]:
    """Rewrite the 16-bit mono wav at `path` as: gap, chunk1, gap, chunk2, gap, ...
    Returns the mapping [(track_start, track_end, original_start)] for remap_time()."""
    import array
    import wave
    with wave.open(str(path), "rb") as w:
        params = w.getparams()
        src = array.array("h", w.readframes(w.getnframes()))
    sr = params.framerate
    silence = array.array("h", bytes(int(gap * sr) * 2))
    out = array.array("h")
    out.extend(silence)
    mapping = []
    for a, b in intervals:
        i, j = max(0, int(a * sr)), min(len(src), int(b * sr))
        if j <= i:
            continue
        t0 = len(out) / sr
        out.extend(src[i:j])
        mapping.append((round(t0, 4), round(len(out) / sr, 4), i / sr))
        out.extend(silence)
    with wave.open(str(path), "wb") as w:
        w.setparams(params)
        w.writeframes(out.tobytes())
    return mapping


def remap_time(t: float, mapping: list[tuple[float, float, float]]) -> float:
    """Speech-track time → original time (times in the pauses snap to the nearest chunk edge)."""
    if not mapping:
        return t
    best = min(mapping, key=lambda m: 0 if m[0] <= t <= m[1] else min(abs(t - m[0]), abs(t - m[1])))
    a, b, orig = best
    return orig + min(max(t, a), b) - a


def fit_to_speech(words: list[dict], intervals: list[tuple[float, float]],
                  tol: float = 0.25) -> list[dict]:
    """Drop words that start outside speech; clamp each word into its speech interval."""
    out = []
    for w in words:
        iv = next(((a, b) for a, b in intervals if a - tol <= w["start"] <= b + tol), None)
        if iv is None:
            continue
        a, b = iv
        start = min(max(w["start"], a), b - 0.05)
        end = min(max(w["end"], start + 0.08), b)
        out.append({**w, "start": round(start, 3), "end": round(end, 3)})
    return out


# ---------------------------------------------------------------------------
# running whisper
# ---------------------------------------------------------------------------
AUDIO_FILTER = "highpass=f=80,dynaudnorm=f=150:g=15"


def _ansi_codepage() -> int | None:
    if os.name != "nt":
        return None
    try:
        import ctypes
        return ctypes.windll.kernel32.GetACP()
    except Exception:  # noqa: BLE001
        return None


def utf8_argv(text: str, acp: int | None) -> str:
    """whisper-cli reads ANSI argv on Windows, so 'ğ', 'ş', 'ı' arrive mangled. Pass the UTF-8
    bytes "pre-decoded" with the ANSI code page: Windows re-encodes them back to exactly those
    bytes. Characters whose bytes don't fit the code page are dropped."""
    if not acp or acp == 65001:
        return text
    enc = f"cp{acp}"
    out = []
    for ch in text:
        try:
            out.append(ch.encode("utf-8").decode(enc, errors="strict"))
        except UnicodeDecodeError:
            continue
    return "".join(out)


def build_whisper_cmd(exe: str, model: str, language: str, *, vad_model: str | None,
                      dtw: str | None, prompt: str | None, acp: int | None = None) -> list[str]:
    """vad_model here = whisper-cli's built-in VAD; the app uses its own VAD gate instead."""
    cmd = [exe, "-m", model, "-f", "audio.wav", "-l", language,
           "-ojf", "-of", "transcript", "-pp", "-sns", "-mc", "0"]
    if vad_model:
        cmd += ["--vad", "-vm", vad_model]
    if dtw:
        cmd += ["-dtw", dtw, "-nfa"]  # DTW needs flash attention off
    if prompt and prompt.strip():
        cmd += ["--prompt", utf8_argv(prompt.strip()[:400], acp)]
    return cmd


_whisper_lock = threading.Lock()  # one whisper process at a time (GPU memory)


def transcribe(cfg: dict, clip_id: str, start: float, end: float, report) -> dict:
    if not _whisper_lock.acquire(blocking=False):
        report(0.0, "Sırada… (başka bir altyazı işi bitince başlayacak)")
        _whisper_lock.acquire()
    try:
        return _transcribe(cfg, clip_id, start, end, report)
    finally:
        _whisper_lock.release()


def _transcribe(cfg: dict, clip_id: str, start: float, end: float, report) -> dict:
    info = config.whisper_info(cfg)
    exes, model = info["exes"], info["model"]
    if not exes or model is None:
        raise JobError("whisper.cpp bulunamadı. Önce scripts\\setup_whisper.ps1 çalıştırın.")
    clip = clips.load(clip_id)
    d = clips.clip_dir(clip_id)
    wav = d / "audio.wav"
    wav.unlink(missing_ok=True)

    report(0.02, "Ses çıkarılıyor…")
    af = ["-af", AUDIO_FILTER] if cfg.get("whisper_filter", True) else []
    code, tail = run_process([cfg["ffmpeg"], "-y", "-v", "error",
                              "-ss", f"{start:.3f}", "-to", f"{end:.3f}", "-i", clip["source"],
                              "-vn", *af, "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", "audio.wav"],
                             cwd=str(d))
    if code != 0 or not wav.exists():
        raise JobError("Ses çıkarılamadı: " + " | ".join(tail[-3:]))

    intervals, mapping = None, []
    if cfg.get("whisper_vad", True) and info["vad_model"] and info["vad_exe"]:
        report(0.04, "Konuşma bölümleri bulunuyor (VAD)…")
        try:  # full stdout (run_process keeps only the last lines)
            out = run_simple([str(info["vad_exe"]), "-vm", str(info["vad_model"]),
                              "-f", "audio.wav", "-np"], cwd=str(d), timeout=600)
        except JobError:
            out = None  # VAD unavailable → transcribe without it
        if out is not None:
            intervals = merge_intervals(parse_vad_output(out), end - start)
            if not intervals:
                raise JobError("Bu klipte konuşma bulunamadı (sadece müzik/oyun sesi).")
            mapping = build_speech_track(wav, intervals)

    def on_line(line: str):
        m = _PROGRESS.search(line)
        if m:
            report(0.08 + 0.88 * int(m.group(1)) / 100, f"Altyazı çıkarılıyor ({model.stem.replace('ggml-', '')})… %{m.group(1)}")

    dtw = dtw_preset(model.name) if cfg.get("whisper_dtw", True) else None
    out_json = d / "transcript.json"
    attempts = [(exe, dtw) for exe in exes] + ([(exes[0], None)] if dtw else [])  # last resort: no DTW
    last_err: list[str] = []
    for exe, use_dtw in attempts:
        out_json.unlink(missing_ok=True)
        report(0.08, f"Altyazı çıkarılıyor ({model.stem.replace('ggml-', '')})…")
        cmd = build_whisper_cmd(str(exe), str(model), cfg.get("whisper_language", "tr"),
                                vad_model=None, dtw=use_dtw, prompt=cfg.get("whisper_prompt"),
                                acp=_ansi_codepage())
        code, tail = run_process(cmd, on_line, cwd=str(d))
        if code == 0 and out_json.exists():
            break
        last_err = tail[-4:]
    else:
        raise JobError("whisper hata verdi: " + " | ".join(last_err))

    data = json.loads(out_json.read_bytes().decode("utf-8", errors="replace"))
    words = words_from_tokens(data)
    if intervals:
        for w in words:  # speech-track time → clip time
            w["start"], w["end"] = remap_time(w["start"], mapping), remap_time(w["end"], mapping)
        words = fit_to_speech(words, intervals)
    for w in words:  # cut-relative → source time
        w["start"] = round(w["start"] + start, 3)
        w["end"] = round(w["end"] + start, 3)
    style = clip.get("style") or cfg["style"]
    segs = words_to_segments(words, int(style.get("max_words", 3)), int(style.get("max_chars", 22)))
    segs = [s for s in segs if s["start"] < end]
    for s in segs:
        s["end"] = min(s["end"], round(end, 2))

    result = {}

    def apply(c):
        c["segments"] = merge_segments(c.get("segments"), segs, start, end)
        result["segments"] = c["segments"]
    clips.mutate(clip_id, apply)
    return {"segments": result["segments"], "added": len(segs), "model": model.name}
