"""Kick VOD → TikTok clip maker. Run: python app.py  (opens http://127.0.0.1:5000)"""
import os
import shutil
import sys
import threading
import time
import webbrowser

from flask import Flask, abort, jsonify, request, send_file, send_from_directory

from core import clips, config, downloader, i18n, jobs, presets, render, transcribe
from core.i18n import t
from core.timeparse import parse_range

HOST, PORT = "127.0.0.1", int(os.environ.get("PORT", "5000"))

app = Flask(__name__, static_folder="static", static_url_path="/static")
config.ensure_dirs()
ENCODERS: dict = {}


@app.before_request
def _set_language() -> None:
    """UI language for server messages: X-Lang header (sent by app.js), else English."""
    i18n.set_lang(request.headers.get("X-Lang"))


def cfg() -> dict:
    return config.load()


def detect_encoders() -> None:
    """Listing the encoder isn't enough (driver may be too old) → do a tiny real encode."""
    try:
        jobs.run_simple([cfg()["ffmpeg"], "-hide_banner", "-v", "error", "-f", "lavfi",
                         "-i", "color=c=black:s=256x256:d=0.2", "-c:v", "h264_nvenc",
                         "-f", "null", "-"], timeout=30)
        ENCODERS["h264_nvenc"] = True
    except jobs.JobError:
        ENCODERS["h264_nvenc"] = False


def get_clip_or_404(clip_id: str) -> dict:
    try:
        clip = clips.load(clip_id)
    except ValueError:
        abort(400)
    if clip is None:
        abort(404)
    return clip


def err(msg: str, code: int = 400):
    return jsonify({"error": msg}), code


@app.get("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.get("/api/status")
def status():
    c = cfg()
    w = config.whisper_info(c)
    exes, model = w["exes"], w["model"]
    return jsonify({
        "ytdlp": bool(shutil.which(c["ytdlp"])),
        "ffmpeg": bool(shutil.which(c["ffmpeg"])),
        "whisper": bool(exes and model),
        "whisper_model": model.name if model else None,
        "whisper_models": [{"name": m.name, "label": config.model_label(m)}
                           for m in w["models"]],
        "whisper_vad": bool(w["vad_model"]),
        "nvenc": ENCODERS.get("h264_nvenc", False),
        "style": c["style"],
    })


@app.get("/api/clips")
def list_clips():
    return jsonify(clips.list_all())


@app.post("/api/clips")
def create_clip():
    body = request.get_json(force=True) or {}
    url = (body.get("url") or "").strip()
    if not url.startswith(("http://", "https://")):
        return err(t("bad_url"))
    try:
        start, end = parse_range(body.get("start", ""), body.get("end", ""))
    except ValueError as e:
        return err(str(e))
    c = cfg()
    if end is not None and end - start > c["max_source_seconds"]:
        return err(t("range_too_long", hours=c["max_source_seconds"] // 3600))
    clip = {"id": clips.new_id(), "url": url, "start": start, "end": end,
            "status": "downloading", "created": time.time(),
            "regions": None, "modes": {"cam": "blur", "game": "blur"},
            "segments": [], "cuts": [], "style": c["style"]}
    clips.save(clip)
    job_id = jobs.start("download", lambda rep: downloader.download(c, clip, rep), clip["id"])
    return jsonify({"clip_id": clip["id"], "job_id": job_id})


@app.get("/api/clips/<clip_id>")
def get_clip(clip_id):
    clip = get_clip_or_404(clip_id)
    if clip.get("cuts") is None and clip.get("video"):
        clip = clips.mutate(clip_id, clips.ensure_cuts)
    clip.pop("words", None)
    clip["preset"] = presets.get(clip.get("channel"))
    running = {"download": jobs.find_running(clip_id, "download")}
    for cut in clip.get("cuts") or []:
        for kind in ("render", "transcribe"):
            j = jobs.find_running(clip_id, f"{kind}:{cut['id']}")
            if j:
                running[f"{kind}:{cut['id']}"] = j
    clip["jobs"] = running
    return jsonify(clip)


def _apply_state(clip: dict, body: dict) -> None:
    for key in ("regions", "modes", "segments", "style"):
        if key in body:
            clip[key] = body[key]
    if "split" in body:
        clip["split"] = render.clamp_split(body["split"])
    if isinstance(body.get("cuts"), list):
        # Render outputs are owned by the server — never let a stale UI copy erase them.
        outputs = {c["id"]: c.get("output") for c in clip.get("cuts") or []}
        cuts = []
        for c in body["cuts"]:
            if not c.get("id"):
                continue
            cuts.append({"id": str(c["id"])[:16], "name": str(c.get("name") or t("clip_default_name", n=1))[:60],
                         "start": float(c["start"]), "end": float(c["end"]),
                         "regions": c.get("regions"), "modes": c.get("modes"),
                         "split": render.clamp_split(c["split"]) if c.get("split") is not None else None,
                         "output": outputs.get(c["id"])})
        clip["cuts"] = cuts


@app.put("/api/clips/<clip_id>/state")
def save_state(clip_id):
    get_clip_or_404(clip_id)
    body = request.get_json(force=True) or {}
    clip = clips.mutate(clip_id, lambda c: _apply_state(c, body))
    return jsonify({"ok": True, "cuts": clip.get("cuts")})


@app.delete("/api/clips/<clip_id>")
def delete_clip(clip_id):
    get_clip_or_404(clip_id)
    clips.delete(clip_id)
    return jsonify({"ok": True})


@app.get("/api/clips/<clip_id>/video")
def clip_video(clip_id):
    clip = get_clip_or_404(clip_id)
    if not clip.get("source"):
        abort(404)
    return send_file(clips.clip_dir(clip_id) / clip["source"], mimetype="video/mp4", conditional=True)


def _cut_or_error(clip_id: str):
    """Save any state sent with the request, then return (clip, cut) or an error response."""
    get_clip_or_404(clip_id)
    body = request.get_json(silent=True) or {}
    clip = clips.mutate(clip_id, lambda c: _apply_state(c, body))
    if not clip.get("source"):
        return None, None, err(t("download_first"))
    return clip, body, None


@app.post("/api/clips/<clip_id>/cuts/<cut_id>/transcribe")
def start_transcribe(clip_id, cut_id):
    clip, body, error = _cut_or_error(clip_id)
    if error:
        return error
    cut = clips.find_cut(clip, cut_id)
    if cut is None:
        return err(t("cut_not_found"), 404)
    kind = f"transcribe:{cut_id}"
    running = jobs.find_running(clip_id, kind)
    if running:
        return jsonify({"job_id": running})
    c = cfg()
    if body.get("language") in ("tr", "en", "auto"):
        c["whisper_language"] = body["language"]
    installed = {m.name for m in config.whisper_info(c)["models"]}
    if body.get("model") in installed:
        c["whisper_model"] = body["model"]
    if isinstance(body.get("vad"), bool):
        c["whisper_vad"] = body["vad"]
    if isinstance(body.get("prompt"), str):
        c["whisper_prompt"] = body["prompt"]
    start, end = cut["start"], cut["end"]
    return jsonify({"job_id": jobs.start(
        kind, lambda rep: transcribe.transcribe(c, clip_id, start, end, rep), clip_id)})


def _start_render_job(clip: dict, cut: dict) -> str:
    kind = f"render:{cut['id']}"
    running = jobs.find_running(clip["id"], kind)
    if running:
        return running
    c, clip_id, cut_id = cfg(), clip["id"], cut["id"]
    ticket = render.take_ticket()
    return jobs.start(kind, lambda rep: render.render(c, clip_id, cut_id, rep, ENCODERS, ticket), clip_id)


def _layout_error(clip: dict, cut: dict) -> str | None:
    regions, _, _ = render.effective_layout(clip, cut)
    if not (regions.get("cam") and regions.get("game")):
        return t("select_regions")
    if cut["end"] - cut["start"] < 0.5:
        return t("cut_too_short", name=cut["name"])
    return None


@app.post("/api/clips/<clip_id>/cuts/<cut_id>/render")
def start_render(clip_id, cut_id):
    clip, _, error = _cut_or_error(clip_id)
    if error:
        return error
    cut = clips.find_cut(clip, cut_id)
    if cut is None:
        return err(t("cut_not_found"), 404)
    problem = _layout_error(clip, cut)
    if problem:
        return err(problem)
    return jsonify({"job_id": _start_render_job(clip, cut)})


@app.post("/api/clips/<clip_id>/render-all")
def render_all(clip_id):
    clip, _, error = _cut_or_error(clip_id)
    if error:
        return error
    cuts = clip.get("cuts") or []
    if not cuts:
        return err(t("add_cut_first"))
    for cut in cuts:
        problem = _layout_error(clip, cut)
        if problem:
            return err(problem)
    return jsonify({"jobs": {cut["id"]: _start_render_job(clip, cut) for cut in cuts}})


@app.get("/api/jobs/<job_id>")
def job_status(job_id):
    job = jobs.get(job_id)
    if job is None:
        abort(404)
    return jsonify(job)


@app.get("/api/presets")
def list_presets():
    return jsonify(presets.all_presets())


@app.post("/api/presets")
def save_preset():
    body = request.get_json(force=True) or {}
    if not body.get("channel") or not body.get("preset"):
        return err(t("missing_data"))
    presets.put(body["channel"], body["preset"])
    return jsonify({"ok": True})


@app.post("/api/style")
def save_style():
    config.save_style(request.get_json(force=True) or {})
    return jsonify({"ok": True})


@app.get("/output/<path:name>")
def output_file(name):
    return send_from_directory(config.OUTPUT_DIR, name, as_attachment=request.args.get("dl") == "1")


@app.post("/api/open-output")
def open_output():
    if os.name == "nt":
        os.startfile(config.OUTPUT_DIR)  # noqa: S606
    return jsonify({"ok": True})


if __name__ == "__main__":
    detect_encoders()
    if "--no-browser" not in sys.argv:
        threading.Timer(1.0, lambda: webbrowser.open(f"http://{HOST}:{PORT}")).start()
    app.run(host=HOST, port=PORT, debug=False, threaded=True)
