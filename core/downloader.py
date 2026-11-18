"""Download only [start, end] of a Kick VOD with yt-dlp, then probe it."""
import json
import re

from . import clips
from .jobs import JobError, run_process, run_simple
from .timeparse import fmt_time

_PCT = re.compile(r"\[download\]\s+([\d.]+)%")
_FFTIME = re.compile(r"time=(\d+):(\d+):(\d+(?:\.\d+)?)")


def fetch_metadata(cfg: dict, url: str) -> dict:
    out = run_simple([cfg["ytdlp"], "--no-warnings", "--skip-download", "--no-playlist",
                      "--print", "%(channel,uploader,uploader_id)s",
                      "--print", "%(title)s",
                      "--print", "%(duration)s", url], timeout=90)
    lines = out.strip().splitlines()
    if len(lines) < 3:
        raise JobError("Video bilgisi alınamadı. Link doğru mu?")
    channel, title, duration = lines[-3:]
    try:
        dur = float(duration)
    except ValueError:
        dur = None
    return {"channel": channel if channel != "NA" else "bilinmeyen",
            "title": title, "duration": dur}


def probe(cfg: dict, path: str) -> dict:
    out = run_simple([cfg["ffprobe"], "-v", "error", "-print_format", "json",
                      "-show_streams", "-show_format", path])
    data = json.loads(out)
    v = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), None)
    if v is None:
        raise JobError("İndirilen dosyada video yok")
    num, _, den = (v.get("avg_frame_rate") or v.get("r_frame_rate") or "30/1").partition("/")
    fps = float(num) / float(den or 1) if float(den or 1) else 30.0
    has_audio = any(s.get("codec_type") == "audio" for s in data["streams"])
    a = next((s for s in data["streams"] if s.get("codec_type") == "audio"), None)
    return {"width": int(v["width"]), "height": int(v["height"]), "fps": round(fps, 3),
            "duration": float(data["format"].get("duration", 0)), "has_audio": has_audio,
            "vcodec": v.get("codec_name"), "acodec": a.get("codec_name") if a else None}


# Settings for the browser-compatibility fallback transcode.
ENCODE_ARGS = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p",
               "-c:a", "aac", "-b:a", "192k"]


def make_browser_playable(cfg: dict, clip: dict, report) -> None:
    """Safety net: transcode source to H.264/AAC if the browser can't play it."""
    v = clip["video"]
    if v.get("vcodec") == "h264" and v.get("acodec") in (None, "aac", "mp3"):
        return
    d = clips.clip_dir(clip["id"])
    duration = v.get("duration") or 1

    def on_line(line: str):
        if line.startswith("out_time_us="):
            try:
                t = int(line.split("=", 1)[1]) / 1_000_000
            except ValueError:
                return
            report(0.5 + 0.48 * min(1.0, t / duration), "Tarayıcı uyumlu formata çevriliyor…")

    report(0.5, "Tarayıcı uyumlu formata çevriliyor…")
    code, tail = run_process([cfg["ffmpeg"], "-y", "-hide_banner", "-nostats", "-progress", "pipe:1",
                              "-i", clip["source"], "-map", "0:v:0", "-map", "0:a:0?", *ENCODE_ARGS,
                              "-movflags", "+faststart", "source_h264.mp4"], on_line, cwd=str(d))
    out = d / "source_h264.mp4"
    if code != 0 or not out.exists():
        raise JobError("Video dönüştürülemedi: " + " | ".join(tail[-3:]))
    src = d / clip["source"]
    src.unlink()
    out.replace(d / "source.mp4")
    clip["source"] = "source.mp4"
    clip["video"] = probe(cfg, str(d / "source.mp4"))
    clips.save(clip)


def download(cfg: dict, clip: dict, report) -> dict:
    d = clips.clip_dir(clip["id"])
    start = clip["start"]

    report(0.02, "Video bilgisi alınıyor…")
    meta = fetch_metadata(cfg, clip["url"])
    vod_dur = meta["duration"]
    if vod_dur and start >= vod_dur:
        raise JobError(f"Başlangıç ({fmt_time(start)}) videonun süresinden "
                       f"({fmt_time(vod_dur)}) sonra")
    end = clip.get("end")
    if end is None:  # empty end → until the end of the VOD
        if not vod_dur:
            raise JobError("VOD süresi bilinmiyor; bitiş zamanını girin")
        end = vod_dur
    if vod_dur:
        end = min(end, vod_dur)
    if end - start > cfg["max_source_seconds"]:
        raise JobError(f"Aralık en fazla {cfg['max_source_seconds'] // 3600} saat olabilir")
    length = end - start
    clip.update(channel=meta["channel"], title=meta["title"], vod_duration=vod_dur, end=end)
    clips.save(clip)

    report(0.05, f"İndiriliyor: {fmt_time(start)} → {fmt_time(end)}")
    for old in d.glob("source.*"):
        old.unlink()

    def on_line(line: str):
        m = _PCT.search(line)
        if m:
            report(0.05 + 0.9 * float(m.group(1)) / 100, f"İndiriliyor… %{float(m.group(1)):.0f}")
            return
        m = _FFTIME.search(line)
        if m and length > 0:
            t = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
            report(0.05 + 0.9 * min(1.0, t / length), f"İndiriliyor… {fmt_time(t)} / {fmt_time(length)}")

    # Stream copy (no re-encode): fast, keeps Kick's H.264/AAC. The cut snaps to the previous
    # keyframe, which is fine — exact clip boundaries are applied at render time.
    cmd = [cfg["ytdlp"], "--newline", "--no-playlist", "--no-part",
           "--download-sections", f"*{start:.3f}-{end:.3f}",
           "-f", "bv*[height<=1080]+ba/b[height<=1080]/bv*+ba/b",
           "--merge-output-format", "mp4", "--remux-video", "mp4",
           "-o", str(d / "source.%(ext)s"), clip["url"]]
    if cfg["ffmpeg"] != "ffmpeg":
        cmd[1:1] = ["--ffmpeg-location", cfg["ffmpeg"]]
    code, tail = run_process(cmd, on_line)
    files = sorted(d.glob("source.mp4")) or sorted(d.glob("source.*"))
    if code != 0 or not files:
        raise JobError("İndirme başarısız: " + " | ".join(tail[-4:]))

    report(0.97, "Video inceleniyor…")
    info = probe(cfg, str(files[0]))
    clip.update(source=files[0].name, video=info)
    make_browser_playable(cfg, clip, report)
    clip["status"] = "ready"
    clips.ensure_cuts(clip)
    clips.save(clip)
    return {"clip_id": clip["id"]}
