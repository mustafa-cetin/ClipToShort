"""Background jobs with progress, plus a subprocess runner that streams lines."""
import os
import re
import subprocess
import threading
import traceback
import uuid
from typing import Callable

_jobs: dict[str, dict] = {}
_lock = threading.Lock()

CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


class JobError(Exception):
    """Error with a user-facing message."""


def start(kind: str, fn: Callable[[Callable[..., None]], dict | None], clip_id: str | None = None) -> str:
    job_id = uuid.uuid4().hex[:10]
    job = {"id": job_id, "kind": kind, "clip_id": clip_id, "status": "running",
           "progress": 0.0, "message": "Başlıyor…", "result": None, "error": None, "log": []}
    with _lock:
        _jobs[job_id] = job

    def report(progress: float | None = None, message: str | None = None):
        with _lock:
            if progress is not None:
                job["progress"] = max(0.0, min(1.0, progress))
            if message is not None:
                job["message"] = message

    def run():
        try:
            result = fn(report)
            with _lock:
                job.update(status="done", progress=1.0, result=result, message="Tamamlandı")
        except JobError as e:
            with _lock:
                job.update(status="error", error=str(e), message=str(e))
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            with _lock:
                job.update(status="error", error=f"{type(e).__name__}: {e}", message="Hata")

    threading.Thread(target=run, daemon=True).start()
    return job_id


def get(job_id: str) -> dict | None:
    with _lock:
        job = _jobs.get(job_id)
        return {k: v for k, v in job.items() if k != "log"} if job else None


def find_running(clip_id: str, kind: str) -> str | None:
    with _lock:
        for j in _jobs.values():
            if j["clip_id"] == clip_id and j["kind"] == kind and j["status"] == "running":
                return j["id"]
    return None


def run_process(cmd: list[str], on_line: Callable[[str], None] | None = None,
                cwd: str | None = None) -> tuple[int, list[str]]:
    """Run cmd, merging stdout/stderr, splitting output on \\r and \\n.
    Returns (returncode, last ~60 lines)."""
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL, cwd=cwd,
                                creationflags=CREATE_NO_WINDOW)
    except FileNotFoundError:
        raise JobError(f"Program bulunamadı: {cmd[0]}") from None
    tail: list[str] = []
    buf = b""
    assert proc.stdout is not None
    while True:
        chunk = proc.stdout.read1(4096) if hasattr(proc.stdout, "read1") else proc.stdout.read(4096)
        if not chunk:
            break
        buf += chunk
        parts = re.split(rb"[\r\n]", buf)
        buf = parts.pop()
        for raw in parts:
            line = raw.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            tail.append(line)
            if len(tail) > 60:
                tail.pop(0)
            if on_line:
                on_line(line)
    if buf.strip():
        line = buf.decode("utf-8", errors="replace").strip()
        tail.append(line)
        if on_line:
            on_line(line)
    return proc.wait(), tail


def run_simple(cmd: list[str], cwd: str | None = None, timeout: int = 120) -> str:
    try:
        r = subprocess.run(cmd, capture_output=True, cwd=cwd, timeout=timeout,
                           stdin=subprocess.DEVNULL, creationflags=CREATE_NO_WINDOW)
    except FileNotFoundError:
        raise JobError(f"Program bulunamadı: {cmd[0]}") from None
    if r.returncode != 0:
        err = r.stderr.decode("utf-8", errors="replace").strip().splitlines()[-5:]
        raise JobError(f"{os.path.basename(cmd[0])} hata verdi: " + " | ".join(err))
    return r.stdout.decode("utf-8", errors="replace")
