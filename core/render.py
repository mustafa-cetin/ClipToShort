"""Build the ffmpeg filtergraph (camera top, game bottom, captions on the seam) and render cuts."""
import os
import re
import shutil
import threading

from . import clips, subtitles
from .config import FONTS_DIR, OUTPUT_DIR
from .jobs import JobError, run_process
from .timeparse import fmt_time

OUT_W, OUT_H = 1080, 1920
DEFAULT_SPLIT = 960                   # camera section height (top); game gets the rest
SPLIT_MIN, SPLIT_MAX = 384, 1536      # 20 % … 80 %
MODES = ("blur", "cover")


class _FifoLock:
    """One holder at a time, granted in request order (so "render all" keeps clip order)."""

    def __init__(self):
        self._cond = threading.Condition()
        self._next_ticket = 0
        self._serving = 0

    def take_ticket(self) -> int:
        """Call synchronously when the request arrives, so order = click order."""
        with self._cond:
            ticket = self._next_ticket
            self._next_ticket += 1
            return ticket

    def acquire(self, ticket: int, on_wait=None):
        with self._cond:
            if ticket != self._serving and on_wait:
                on_wait(ticket - self._serving)
            self._cond.wait_for(lambda: self._serving == ticket)

    def release(self):
        with self._cond:
            self._serving += 1
            self._cond.notify_all()


_render_lock = _FifoLock()  # one render at a time; extra jobs wait in line


def clamp_region(r: dict, src_w: int, src_h: int) -> dict:
    """Clamp to the frame and snap to even pixels (yuv420 friendly)."""
    x = max(0, min(int(r["x"]), src_w - 16))
    y = max(0, min(int(r["y"]), src_h - 16))
    w = max(16, min(int(r["w"]), src_w - x))
    h = max(16, min(int(r["h"]), src_h - y))
    x, y = x - x % 2, y - y % 2
    w, h = w - w % 2, h - h % 2
    return {"x": x, "y": y, "w": w, "h": h}


def clamp_split(split) -> int:
    """Camera section height in px: even, within SPLIT_MIN..SPLIT_MAX; bad input → 50/50."""
    try:
        v = int(round(float(split)))
    except (TypeError, ValueError):
        return DEFAULT_SPLIT
    v = max(SPLIT_MIN, min(SPLIT_MAX, v))
    return v - v % 2


def _region_chain(label: str, r: dict, mode: str, h: int) -> str:
    """Crop region r from the source and fit it into a 1080×h section."""
    crop = f"crop={r['w']}:{r['h']}:{r['x']}:{r['y']}"
    if mode == "cover":
        return (f"[0:v]{crop},scale={OUT_W}:{h}:force_original_aspect_ratio=increase,"
                f"crop={OUT_W}:{h},setsar=1[{label}]")
    # blur: blurred "cover" background (blurred at 1/4 size for speed) + sharp "contain" foreground
    qw, qh = OUT_W // 4, max(2, (h // 4) - (h // 4) % 2)
    return (f"[0:v]{crop},split=2[{label}a][{label}b];"
            f"[{label}a]scale={qw}:{qh}:force_original_aspect_ratio=increase,crop={qw}:{qh},"
            f"gblur=sigma=10,scale={OUT_W}:{h},eq=brightness=-0.08,setsar=1[{label}bg];"
            f"[{label}b]scale={OUT_W}:{h}:force_original_aspect_ratio=decrease"
            f":force_divisible_by=2,setsar=1[{label}fg];"
            f"[{label}bg][{label}fg]overlay=(W-w)/2:(H-h)/2[{label}]")


def build_filtergraph(cam: dict, game: dict, cam_mode: str, game_mode: str,
                      ass_file: str | None = None, fonts_dir: str | None = None,
                      max_fps: float | None = None, cam_h: int = DEFAULT_SPLIT) -> str:
    cam_h = clamp_split(cam_h)
    parts = [_region_chain("top", cam, cam_mode, cam_h),
             _region_chain("bot", game, game_mode, OUT_H - cam_h)]
    tail = "[top][bot]vstack=inputs=2"
    if max_fps:
        tail += f",fps={max_fps:g}"
    tail += ",format=yuv420p"
    if ass_file:
        tail += f",ass={ass_file}"
        if fonts_dir:
            tail += f":fontsdir={fonts_dir}"
    parts.append(tail + "[v]")
    return ";".join(parts)


def effective_layout(clip: dict, cut: dict) -> tuple[dict, dict, int]:
    """Cut-specific regions/modes/split override the shared ones."""
    regions = cut.get("regions") or clip.get("regions") or {}
    modes = cut.get("modes") or clip.get("modes") or {}
    modes = {k: modes.get(k) if modes.get(k) in MODES else "blur" for k in ("cam", "game")}
    split = cut.get("split") if cut.get("split") is not None else clip.get("split")
    return regions, modes, clamp_split(split if split is not None else DEFAULT_SPLIT)


def segments_for_cut(segments: list[dict], start: float, end: float) -> list[dict]:
    """Captions overlapping [start, end], shifted to cut time and clipped to the cut."""
    out = []
    for s in segments or []:
        if not (s.get("text") or "").strip() or s["end"] <= start or s["start"] >= end:
            continue
        a, b = max(s["start"], start) - start, min(s["end"], end) - start
        if b - a >= 0.05:
            out.append({"start": round(a, 3), "end": round(b, 3), "text": s["text"]})
    return out


def build_render_cmd(ffmpeg: str, source: str, start: float, end: float, filter_file: str,
                     encoder_args: list[str], has_audio: bool, out_file: str) -> list[str]:
    cmd = [ffmpeg, "-y", "-hide_banner", "-nostats", "-progress", "pipe:1",
           "-ss", f"{start:.3f}", "-to", f"{end:.3f}", "-i", source,
           "-/filter_complex", filter_file, "-map", "[v]"]
    if has_audio:
        cmd += ["-map", "0:a:0?", "-c:a", "aac", "-b:a", "192k", "-ar", "48000"]
    return cmd + encoder_args + ["-pix_fmt", "yuv420p", "-profile:v", "high",
                                 "-movflags", "+faststart", out_file]


def _ffpath(p, cwd) -> str:
    """Relative, forward-slash path → no drive-letter colons to escape in the filtergraph."""
    rel = os.path.relpath(p, cwd).replace("\\", "/")
    if re.search(r"[:,;\[\]' ]", rel):
        raise JobError(f"Yol filtre için uygun değil: {rel}")
    return rel


def _safe_name(s: str) -> str:
    s = re.sub(r"[^\w\-]+", "_", s or "klip", flags=re.UNICODE).strip("_")
    return s[:40] or "klip"


def output_name(clip: dict, cut: dict) -> str:
    vod_t = fmt_time((clip.get("start") or 0) + cut["start"]).replace(":", "-")
    return f"{_safe_name(clip.get('channel'))}_{vod_t}_{_safe_name(cut.get('name'))}_{cut['id'][:4]}.mp4"


def take_ticket() -> int:
    return _render_lock.take_ticket()


def render(cfg: dict, clip_id: str, cut_id: str, report, encoder_ok: dict, ticket: int) -> dict:
    _render_lock.acquire(ticket, on_wait=lambda n: report(0.0, f"Sırada… (önünde {n} render var)"))
    try:
        return _render(cfg, clip_id, cut_id, report, encoder_ok)
    finally:
        _render_lock.release()


def _render(cfg: dict, clip_id: str, cut_id: str, report, encoder_ok: dict) -> dict:
    clip = clips.load(clip_id)  # fresh state (UI may have edited while we waited)
    cut = clips.find_cut(clip, cut_id) if clip else None
    if cut is None:
        raise JobError("Kesim bulunamadı (silinmiş olabilir)")
    d = clips.clip_dir(clip_id)
    v = clip["video"]
    regions, modes, split = effective_layout(clip, cut)
    if not (regions.get("cam") and regions.get("game")):
        raise JobError("Kamera ve oyun bölgelerini seçin")
    cam = clamp_region(regions["cam"], v["width"], v["height"])
    game = clamp_region(regions["game"], v["width"], v["height"])
    start = max(0.0, float(cut["start"]))
    end = min(float(cut["end"]), v.get("duration") or cut["end"])
    if end - start < 0.5:
        raise JobError(f"“{cut.get('name')}” çok kısa")

    tag = cut["id"]
    ass_rel = None
    segs = segments_for_cut(clip.get("segments"), start, end)
    if segs:
        (d / f"subs_{tag}.ass").write_text(
            subtitles.build_ass(segs, clip.get("style") or cfg["style"], seam_y=split),
            encoding="utf-8")
        ass_rel = f"subs_{tag}.ass"

    graph = build_filtergraph(cam, game, modes["cam"], modes["game"], ass_rel,
                              _ffpath(FONTS_DIR, d) if ass_rel else None,
                              60 if v.get("fps", 30) > 60.5 else None, cam_h=split)
    (d / f"filter_{tag}.txt").write_text(graph, encoding="utf-8")

    duration = end - start
    tmp_out = d / f"out_{tag}.mp4"

    def on_line(line: str):
        if line.startswith("out_time_us="):
            try:
                t = int(line.split("=", 1)[1]) / 1_000_000
            except ValueError:
                return
            report(min(0.99, t / duration), f"Render ediliyor… %{min(99, t / duration * 100):.0f}")

    encoders = []
    if encoder_ok.get("h264_nvenc", False):
        encoders.append(["-c:v", "h264_nvenc", "-preset", "p5", "-rc", "vbr", "-cq", "20",
                         "-b:v", "0", "-maxrate", "20M", "-bufsize", "40M"])
    encoders.append(["-c:v", "libx264", "-preset", "medium", "-crf", "19"])

    tail: list[str] = []
    for enc in encoders:
        tmp_out.unlink(missing_ok=True)
        cmd = build_render_cmd(cfg["ffmpeg"], clip["source"], start, end, f"filter_{tag}.txt",
                               enc, v.get("has_audio", True), tmp_out.name)
        report(0.01, f"Render başlıyor ({enc[1]})…")
        code, tail = run_process(cmd, on_line, cwd=str(d))
        if code == 0 and tmp_out.exists():
            break
        if enc[1] == "h264_nvenc":
            encoder_ok["h264_nvenc"] = False  # don't retry NVENC this session
    else:
        raise JobError("Render başarısız: " + " | ".join(tail[-4:]))

    OUTPUT_DIR.mkdir(exist_ok=True)
    name = output_name(clip, cut)
    shutil.move(str(tmp_out), OUTPUT_DIR / name)

    def set_output(c):
        x = clips.find_cut(c, cut_id)
        if x is not None:
            x["output"] = name
    clips.mutate(clip_id, set_output)
    return {"output": name, "cut_id": cut_id}
