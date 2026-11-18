"use strict";
const $ = (s) => document.querySelector(s);
const video = $("#video");
const srcCanvas = $("#srcCanvas");
const srcCtx = srcCanvas.getContext("2d");
const pv = $("#previewCanvas");
const pvCtx = pv.getContext("2d");

const OUT_W = 1080, OUT_H = 1920;
const SPLIT_DEFAULT = 960, SPLIT_MIN = 384, SPLIT_MAX = 1536; // camera height: 20 % … 80 %
const clampSplit = (v) => { v = Math.round(+v || SPLIT_DEFAULT); v = Math.max(SPLIT_MIN, Math.min(SPLIT_MAX, v)); return v - (v % 2); };
let split = SPLIT_DEFAULT;  // shared camera-section height
let seamHover = false, seamDrag = false; // dragging the camera/game seam on the preview
const COLORS = { cam: "#3d9bff", game: "#53fc18" };
const LABELS = { cam: "region.camLabel", game: "region.gameLabel" }; // i18n keys
const GB_PER_HOUR = 2.7;

let clip = null;            // server source object
let regions = null;         // shared {cam, game} in source pixels
let modes = { cam: "blur", game: "blur" };
let cuts = [];              // [{id, name, start, end, regions, modes, output}]
let segments = [];          // captions on the SOURCE timeline
let style = {};
let defaultStyle = {};
let selId = null;           // selected cut id
let active = "cam";         // region being drawn
let inPt = null, outPt = null;
let playUntil = null;       // stop playback here (cut playback)
const cutStatus = {};       // cutId → {render: {...}, transcribe: {...}}

// ---------- helpers ----------
async function api(path, opts = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json", "X-Lang": LANG }, ...opts,
    body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
  return data;
}
function toast(msg, isErr = false) {
  const t = $("#toast");
  t.textContent = msg; t.className = isErr ? "err" : "";
  clearTimeout(toast._t); toast._t = setTimeout(() => t.classList.add("hidden"), 3500);
}
function fmtUnits(sec, decimals) {
  // Round the total first, so 59.9996 → "1:00.000" (not "0:60.000").
  const scale = 10 ** decimals;
  const total = Math.round(Math.max(0, sec || 0) * scale);
  const whole = Math.floor(total / scale), frac = total % scale;
  const h = Math.floor(whole / 3600), m = Math.floor(whole % 3600 / 60), s = whole % 60;
  const ss = `${String(s).padStart(2, "0")}.${String(frac).padStart(decimals, "0")}`;
  return h ? `${h}:${String(m).padStart(2, "0")}:${ss}` : `${m}:${ss}`;
}
function fmt(sec) { return fmtUnits(sec, 1); } // m:ss.s or h:mm:ss.s
function fmtHms(sec) {
  const h = Math.floor(sec / 3600), m = Math.floor(sec % 3600 / 60), s = Math.floor(sec % 60);
  return `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}
function parseT(text) { // "1:02:03.5" | "83.2" → seconds (NaN if invalid)
  const s = String(text).trim().replace(",", ".");
  if (!s) return NaN;
  return s.split(":").reduce((acc, p) => (p.trim() === "" || isNaN(+p) ? NaN : acc * 60 + +p), 0);
}
function debounce(fn, ms) { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; }
function setProgress(el, p, msg, error = false) {
  el.classList.remove("hidden");
  el.classList.toggle("error", error);
  el.querySelector(".bar").style.width = `${Math.round((p || 0) * 100)}%`;
  el.querySelector("span").textContent = msg || "";
}
async function pollJob(jobId, onUpdate) {
  for (;;) {
    const j = await api(`/api/jobs/${jobId}`);
    onUpdate(j);
    if (j.status === "done") return j.result;
    if (j.status === "error") throw new Error(j.error || T("common.error"));
    await new Promise((r) => setTimeout(r, 700));
  }
}
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const dur = () => video.duration || clip?.video?.duration || 0;
const sel = () => cuts.find((c) => c.id === selId) || null;
const clone = (o) => JSON.parse(JSON.stringify(o));

// Regions/modes the selected cut actually uses (and that the editor edits).
function layout() {
  const c = sel();
  if (c && c.regions) {
    if (c.split == null) c.split = split;
    return { regions: c.regions, modes: c.modes || (c.modes = { ...modes }), split: c.split, own: true };
  }
  return { regions, modes, split, own: false };
}
function setSplit(v) { // writes to the cut when it has its own layout, else to the shared one
  v = clampSplit(v);
  const c = sel();
  if (c && c.regions) c.split = v; else split = v;
  syncSplitUi(); drawAll(); saveState();
}
function syncSplitUi() {
  const L = layout();
  const pct = Math.round(L.split / OUT_H * 100);
  $("#splitRange").value = pct;
  $("#splitLabel").textContent = T("split.ratio", { cam: pct, game: 100 - pct }) + (L.own ? T("split.own") : "");
  document.querySelectorAll(".splitBtns button").forEach((b) => b.classList.toggle("on", +b.dataset.p === pct));
}
$("#splitRange").addEventListener("input", (e) => setSplit(+e.target.value / 100 * OUT_H));
document.querySelectorAll(".splitBtns button").forEach((b) => {
  b.onclick = () => setSplit(+b.dataset.p / 100 * OUT_H);
});
// Drag the seam directly on the preview.
function pvYToOut(e) { const r = pv.getBoundingClientRect(); return (e.clientY - r.top) / r.height * OUT_H; }
function nearSeam(e) {
  const r = pv.getBoundingClientRect();
  return Math.abs(pvYToOut(e) - layout().split) < 14 / r.height * OUT_H; // within ~14 css px
}
pv.addEventListener("pointerdown", (e) => {
  if (!clip || !nearSeam(e)) return;
  seamDrag = true;
  pv.setPointerCapture(e.pointerId);
});
pv.addEventListener("pointermove", (e) => {
  if (!clip) return;
  if (seamDrag) { setSplit(pvYToOut(e)); return; }
  const h = nearSeam(e);
  if (h !== seamHover) { seamHover = h; pv.classList.toggle("seam", h); drawAll(); }
});
pv.addEventListener("pointerup", () => { if (seamDrag) { seamDrag = false; drawAll(); } });
pv.addEventListener("pointerleave", () => { if (seamHover && !seamDrag) { seamHover = false; pv.classList.remove("seam"); drawAll(); } });

// ---------- status & recent ----------
async function loadStatus() {
  const s = await api("/api/status");
  defaultStyle = s.style;
  const chips = [
    ["yt-dlp", s.ytdlp], ["ffmpeg", s.ffmpeg],
    [s.whisper ? `whisper (${s.whisper_model})` : T("w.none"), s.whisper],
    [s.nvenc ? "NVENC" : "CPU encode", true],
  ];
  $("#status").innerHTML = chips.map(([n, ok]) => `<span class="chip ${ok ? "ok" : "bad"}">${esc(n)}</span>`).join("");
  const modelSel = $("#wModel");
  modelSel.innerHTML = (s.whisper_models || []).map((m) => `<option value="${esc(m.name)}">${esc(m.label)}</option>`).join("");
  let saved = null;
  try { saved = localStorage.getItem("wModel"); } catch (e) { /* storage blocked */ }
  if (saved && (s.whisper_models || []).some((m) => m.name === saved)) modelSel.value = saved;
  else if (s.whisper_model) modelSel.value = s.whisper_model;
  $("#wVad").disabled = !s.whisper_vad;
  if (!s.whisper_vad) { $("#wVad").checked = false; $("#wVad").parentElement.title = T("w.noVad"); }
}
$("#wModel").onchange = (e) => { try { localStorage.setItem("wModel", e.target.value); } catch (err) { /* ignore */ } };
async function loadRecent() {
  const list = await api("/api/clips");
  const ul = $("#recent");
  ul.innerHTML = "";
  $("#recentWrap").classList.toggle("hidden", !list.length);
  for (const c of list) {
    const li = document.createElement("li");
    const len = c.end != null ? fmtHms(c.end - c.start) : "?";
    li.innerHTML = `<span class="t"></span><span class="mono">${esc(T("recent.counts", { cuts: c.cuts, rendered: c.rendered }))}</span><button title="${esc(T("common.delete"))}">✕</button>`;
    li.querySelector(".t").textContent = `${c.channel || "?"} · ${fmtHms(c.start)}–${c.end != null ? fmtHms(c.end) : T("recent.end")} (${len}) · ${c.title || ""}`;
    li.onclick = () => openClip(c.id);
    li.querySelector("button").onclick = async (e) => {
      e.stopPropagation();
      if (!confirm(T("recent.confirmDelete"))) return;
      await api(`/api/clips/${c.id}`, { method: "DELETE" });
      if (clip && clip.id === c.id) { $("#editor").classList.add("hidden"); clip = null; video.removeAttribute("src"); }
      loadRecent();
    };
    ul.appendChild(li);
  }
}

// ---------- download ----------
function updateSizeHint() {
  const a = $("#start").value.trim() ? parseT($("#start").value) : 0;
  const e = $("#end").value.trim();
  const rel = e.startsWith("+") ? e.slice(1) : null;
  const relSec = rel == null ? NaN : /^\d+(\.\d+)?m$/i.test(rel) ? parseFloat(rel) * 60
    : /^\d+(\.\d+)?h$/i.test(rel) ? parseFloat(rel) * 3600 : parseT(rel.replace(/s$/i, ""));
  const b = rel != null ? a + relSec : parseT(e);
  if (!e) { $("#sizeHint").textContent = T("dl.hintOpenEnd"); return; }
  if (isNaN(a) || isNaN(b) || b <= a) { $("#sizeHint").textContent = ""; return; }
  const h = (b - a) / 3600;
  $("#sizeHint").textContent = T("dl.hintSize", { len: fmtHms(b - a), gb: (h * GB_PER_HOUR).toFixed(h < 0.4 ? 2 : 1) });
}
$("#start").addEventListener("input", updateSizeHint);
$("#end").addEventListener("input", updateSizeHint);

$("#dlForm").onsubmit = async (e) => {
  e.preventDefault();
  const btn = e.submitter; btn.disabled = true;
  const el = $("#dlProgress");
  try {
    setProgress(el, 0, T("dl.starting"));
    const r = await api("/api/clips", { method: "POST", body: { url: $("#url").value, start: $("#start").value, end: $("#end").value } });
    await pollJob(r.job_id, (j) => setProgress(el, j.progress, j.message, j.status === "error"));
    setProgress(el, 1, T("dl.done"));
    await openClip(r.clip_id);
    loadRecent();
  } catch (err) {
    setProgress(el, 1, err.message, true);
  } finally { btn.disabled = false; }
};

// ---------- open source ----------
async function openClip(id) {
  const c = await api(`/api/clips/${id}`);
  if (c.jobs && c.jobs.download) {
    const el = $("#dlProgress");
    try { await pollJob(c.jobs.download, (j) => setProgress(el, j.progress, j.message, j.status === "error")); }
    catch (err) { setProgress(el, 1, err.message, true); return; }
    return openClip(id);
  }
  if (!c.source) { toast(T("open.incomplete"), true); return; }
  clip = c;
  location.hash = `clip=${id}`;
  const v = c.video;
  modes = c.modes || { cam: "blur", game: "blur" };
  split = clampSplit(c.split ?? c.preset?.split ?? SPLIT_DEFAULT);
  segments = (c.segments || []).map((s) => ({ ...s }));
  undoStack.length = 0; // undo history belongs to the source that was open
  cuts = (c.cuts || []).map((x) => ({ ...x }));
  style = { ...defaultStyle, ...(c.style || {}) };
  selId = cuts.length ? cuts[0].id : null;
  inPt = outPt = null;
  for (const k of Object.keys(cutStatus)) delete cutStatus[k];

  if (c.regions && c.regions.cam && c.regions.game) {
    regions = c.regions;
  } else if (c.preset && c.preset.regions) {
    applyPreset(c.preset);
    toast(T("preset.loaded", { channel: c.channel }));
    saveState();
  } else {
    regions = {
      cam: { x: 0, y: 0, w: Math.round(v.width * 0.3), h: Math.round(v.height * 0.3) },
      game: { x: 0, y: 0, w: v.width, h: v.height },
    };
  }
  $("#clipInfo").textContent = `${c.channel} · VOD ${fmtHms(c.start)}–${fmtHms(c.end ?? c.start + v.duration)} · ${v.width}×${v.height} ${Math.round(v.fps)}fps`;
  fillStyleInputs();
  $("#wPrompt").value = (c.preset && c.preset.prompt) || "";
  $("#renderProgress").classList.add("hidden");
  $("#trProgress").classList.add("hidden");
  $("#editor").classList.remove("hidden");

  video.src = `/api/clips/${id}/video`;
  video.load();
  resizeSrcCanvas();
  refreshAll();
  // Re-attach to jobs still running on the server.
  for (const [key, jobId] of Object.entries(c.jobs || {})) {
    if (!jobId) continue;
    const [kind, cutId] = key.split(":");
    if (cutId) watchCutJob(kind, cutId, jobId);
  }
}

function applyPreset(p) {
  const v = clip.video;
  const sx = v.width / (p.src_w || v.width), sy = v.height / (p.src_h || v.height);
  const sc = (r) => ({ x: Math.round(r.x * sx), y: Math.round(r.y * sy), w: Math.round(r.w * sx), h: Math.round(r.h * sy) });
  regions = { cam: sc(p.regions.cam), game: sc(p.regions.game) };
  if (p.modes) modes = { ...p.modes };
}

function statePayload() { return { regions, modes, split, segments, style, cuts }; }
const saveState = debounce(() => {
  if (!clip) return;
  api(`/api/clips/${clip.id}/state`, { method: "PUT", body: statePayload() }).catch((e) => toast(e.message, true));
}, 600);

function refreshAll() {
  renderCutTable(); renderTimeline(); renderSegTable(); renderPvSegs(); syncRegionUi(); syncSplitUi(); drawAll();
}

// ---------- source canvas / region editing ----------
let scale = 1;
function resizeSrcCanvas() {
  if (!clip) return;
  const v = clip.video;
  const w = $("#srcWrap").clientWidth || 800;
  const dpr = window.devicePixelRatio || 1;
  srcCanvas.width = Math.round(w * dpr);
  srcCanvas.height = Math.round(w * v.height / v.width * dpr);
  scale = srcCanvas.width / v.width;
  drawAll();
}
window.addEventListener("resize", () => { resizeSrcCanvas(); renderTimeline(); });

function corners(r) { return [[r.x, r.y], [r.x + r.w, r.y], [r.x, r.y + r.h], [r.x + r.w, r.y + r.h]]; }

function drawSrc() {
  const W = srcCanvas.width, H = srcCanvas.height;
  srcCtx.fillStyle = "#000"; srcCtx.fillRect(0, 0, W, H);
  if (video.readyState >= 2) srcCtx.drawImage(video, 0, 0, W, H);
  const L = layout();
  if (!L.regions) return;
  const dpr = window.devicePixelRatio || 1;
  for (const k of active === "cam" ? ["game", "cam"] : ["cam", "game"]) {
    const r = L.regions[k];
    const x = r.x * scale, y = r.y * scale, w = r.w * scale, h = r.h * scale;
    srcCtx.fillStyle = COLORS[k] + (k === active ? "22" : "10");
    srcCtx.fillRect(x, y, w, h);
    srcCtx.lineWidth = (k === active ? 3 : 1.5) * dpr;
    srcCtx.strokeStyle = COLORS[k];
    srcCtx.setLineDash(k === active ? [] : [6 * dpr, 4 * dpr]);
    srcCtx.strokeRect(x, y, w, h);
    srcCtx.setLineDash([]);
    srcCtx.font = `bold ${12 * dpr}px system-ui`;
    const label = `${T(LABELS[k])}${L.own ? T("region.ownTag") : ""} ${r.w}×${r.h}`;
    const tw = srcCtx.measureText(label).width + 10 * dpr;
    srcCtx.fillStyle = COLORS[k];
    srcCtx.fillRect(x, y, tw, 18 * dpr);
    srcCtx.fillStyle = k === "cam" ? "#fff" : "#0b0d10";
    srcCtx.fillText(label, x + 5 * dpr, y + 13 * dpr);
    if (k === active) {
      srcCtx.fillStyle = "#fff";
      for (const [hx, hy] of corners(r)) srcCtx.fillRect(hx * scale - 5 * dpr, hy * scale - 5 * dpr, 10 * dpr, 10 * dpr);
    }
  }
}

function toSrc(e) {
  const rect = srcCanvas.getBoundingClientRect();
  const v = clip.video;
  return {
    x: Math.max(0, Math.min(v.width, (e.clientX - rect.left) / rect.width * v.width)),
    y: Math.max(0, Math.min(v.height, (e.clientY - rect.top) / rect.height * v.height)),
  };
}
const tolSrc = () => 12 / (srcCanvas.getBoundingClientRect().width / clip.video.width);

let drag = null;
srcCanvas.addEventListener("pointerdown", (e) => {
  if (!clip) return;
  srcCanvas.setPointerCapture(e.pointerId);
  const p = toSrc(e), tol = tolSrc();
  const r = layout().regions[active];
  // Corner → resize, inside → move, elsewhere → draw a new rect for the active region.
  const ci = corners(r).findIndex(([cx, cy]) => Math.abs(cx - p.x) < tol && Math.abs(cy - p.y) < tol);
  if (ci >= 0) drag = { type: "resize", anchor: corners(r)[3 - ci] };
  else if (p.x > r.x && p.x < r.x + r.w && p.y > r.y && p.y < r.y + r.h) drag = { type: "move", dx: p.x - r.x, dy: p.y - r.y };
  else drag = { type: "resize", anchor: [p.x, p.y], fresh: true };
});
srcCanvas.addEventListener("pointermove", (e) => {
  if (!clip) return;
  const p = toSrc(e);
  const r = layout().regions[active];
  if (!drag) {
    const tol = tolSrc();
    const onCorner = corners(r).some(([cx, cy]) => Math.abs(cx - p.x) < tol && Math.abs(cy - p.y) < tol);
    const inside = p.x > r.x && p.x < r.x + r.w && p.y > r.y && p.y < r.y + r.h;
    srcCanvas.style.cursor = onCorner ? "nwse-resize" : inside ? "move" : "crosshair";
    return;
  }
  const v = clip.video;
  if (drag.type === "move") {
    r.x = Math.round(Math.max(0, Math.min(v.width - r.w, p.x - drag.dx)));
    r.y = Math.round(Math.max(0, Math.min(v.height - r.h, p.y - drag.dy)));
  } else {
    const [ax, ay] = drag.anchor;
    const w = Math.abs(p.x - ax), h = Math.abs(p.y - ay);
    if (drag.fresh && w < 8 && h < 8) return;
    Object.assign(r, { x: Math.round(Math.min(ax, p.x)), y: Math.round(Math.min(ay, p.y)), w: Math.max(16, Math.round(w)), h: Math.max(16, Math.round(h)) });
  }
  drawAll();
});
srcCanvas.addEventListener("pointerup", () => { if (drag) { drag = null; saveState(); } });

document.querySelectorAll("#activeRegion button").forEach((b) => {
  b.onclick = () => {
    active = b.dataset.r;
    document.querySelectorAll("#activeRegion button").forEach((x) => x.classList.toggle("on", x === b));
    drawAll();
  };
});
$("#camMode").onchange = (e) => { layout().modes.cam = e.target.value; drawAll(); saveState(); };
$("#gameMode").onchange = (e) => { layout().modes.game = e.target.value; drawAll(); saveState(); };

function syncRegionUi() {
  const L = layout(), c = sel();
  $("#camMode").value = L.modes.cam; $("#gameMode").value = L.modes.game;
  document.querySelectorAll("#scope button").forEach((b) => {
    b.classList.toggle("on", (b.dataset.s === "cut") === L.own);
    b.disabled = !c;
  });
  $("#regionHint").textContent = L.own
    ? T("region.hintOwn", { name: c.name })
    : T("region.hintShared");
}
document.querySelectorAll("#scope button").forEach((b) => {
  b.onclick = () => {
    const c = sel();
    if (!c) return;
    if (b.dataset.s === "cut" && !c.regions) {
      c.regions = clone(regions); c.modes = { ...modes }; c.split = split;
    } else if (b.dataset.s === "shared" && c.regions) {
      if (!confirm(T("scope.confirmShared", { name: c.name }))) return;
      c.regions = null; c.modes = null; c.split = null;
    }
    syncRegionUi(); syncSplitUi(); renderCutTable(); drawAll(); saveState();
  };
});
$("#savePreset").onclick = async () => {
  if (!clip) return;
  const L = layout();
  await api("/api/presets", { method: "POST", body: { channel: clip.channel, preset: { src_w: clip.video.width, src_h: clip.video.height, regions: L.regions, modes: L.modes, split: L.split } } });
  toast(T("preset.saved", { channel: clip.channel }));
};

// ---------- preview (WYSIWYG of the ffmpeg graph) ----------
function drawRegion(ctx, r, mode, dx, dy, dw, dh) {
  if (video.readyState < 2) return;
  ctx.save();
  ctx.beginPath(); ctx.rect(dx, dy, dw, dh); ctx.clip();
  const cover = () => {
    const s = Math.max(dw / r.w, dh / r.h);
    const sw = dw / s, sh = dh / s;
    ctx.drawImage(video, r.x + (r.w - sw) / 2, r.y + (r.h - sh) / 2, sw, sh, dx, dy, dw, dh);
  };
  if (mode === "cover") {
    cover();
  } else {
    ctx.filter = `blur(${Math.round(40 * dw / OUT_W)}px) brightness(0.88)`;
    cover();
    ctx.filter = "none";
    const s = Math.min(dw / r.w, dh / r.h);
    const w = r.w * s, h = r.h * s;
    ctx.drawImage(video, r.x, r.y, r.w, r.h, dx + (dw - w) / 2, dy + (dh - h) / 2, w, h);
  }
  ctx.restore();
}
function upper(t) { return style.uppercase ? t.toLocaleUpperCase("tr-TR") : t; }
function wrapLines(ctx, text, maxW) {
  const words = text.split(/\s+/).filter(Boolean);
  const lines = []; let cur = "";
  for (const w of words) {
    const test = cur ? `${cur} ${w}` : w;
    if (cur && ctx.measureText(test).width > maxW) { lines.push(cur); cur = w; } else cur = test;
  }
  if (cur) lines.push(cur);
  return lines;
}
function currentSegment() {
  const t = video.currentTime;
  return segments.find((s) => t >= s.start && t < s.end);
}
function drawCaption(ctx, k) {
  const seg = currentSegment();
  if (!seg || !seg.text.trim()) return;
  const size = (+style.size || 72) * k;
  ctx.save();
  ctx.font = `900 ${size}px "${style.font || "Arial Black"}", "Arial Black", sans-serif`;
  ctx.textAlign = "center"; ctx.textBaseline = "middle"; ctx.lineJoin = "round";
  const lines = wrapLines(ctx, upper(seg.text.trim()), (OUT_W - 120) * k);
  const lh = size * 1.15;
  const cy = (layout().split + (+style.offset_y || 0)) * k; // follows the camera/game seam
  lines.forEach((ln, i) => {
    const y = cy + (i - (lines.length - 1) / 2) * lh;
    if (+style.shadow) { ctx.fillStyle = "rgba(0,0,0,.5)"; ctx.fillText(ln, OUT_W / 2 * k + style.shadow * k, y + style.shadow * k); }
    if (+style.outline) { ctx.strokeStyle = style.outline_color || "#000"; ctx.lineWidth = style.outline * 2 * k; ctx.strokeText(ln, OUT_W / 2 * k, y); }
    ctx.fillStyle = style.color || "#fff";
    ctx.fillText(ln, OUT_W / 2 * k, y);
  });
  ctx.restore();
}
function drawPreview() {
  const k = pv.width / OUT_W;
  pvCtx.fillStyle = "#000"; pvCtx.fillRect(0, 0, pv.width, pv.height);
  const L = layout();
  if (!L.regions) return;
  const camH = L.split * k;
  drawRegion(pvCtx, L.regions.cam, L.modes.cam, 0, 0, pv.width, camH);
  drawRegion(pvCtx, L.regions.game, L.modes.game, 0, camH, pv.width, pv.height - camH);
  drawCaption(pvCtx, k);
  if (seamHover || seamDrag) { // seam handle
    pvCtx.save();
    pvCtx.fillStyle = "rgba(255,255,255,.9)";
    pvCtx.fillRect(0, camH - 1.5, pv.width, 3);
    pvCtx.fillStyle = "rgba(0,0,0,.65)";
    pvCtx.fillRect(pv.width / 2 - 70, camH - 13, 140, 26);
    pvCtx.fillStyle = "#fff"; pvCtx.font = "bold 14px system-ui"; pvCtx.textAlign = "center"; pvCtx.textBaseline = "middle";
    const pct = Math.round(L.split / OUT_H * 100);
    pvCtx.fillText(`⇕ ${T("split.ratio", { cam: pct, game: 100 - pct })}`, pv.width / 2, camH);
    pvCtx.restore();
  }
  const c = sel();
  const t = video.currentTime;
  if (c && (t < c.start - 0.05 || t > c.end + 0.05)) { // outside the selected cut → dim
    pvCtx.fillStyle = "rgba(0,0,0,.55)"; pvCtx.fillRect(0, 0, pv.width, pv.height);
    pvCtx.fillStyle = "#fff"; pvCtx.font = "bold 22px system-ui"; pvCtx.textAlign = "center";
    pvCtx.fillText(T("pv.outside"), pv.width / 2, pv.height / 2);
  }
}

let lastActiveSeg = null;
function drawAll() {
  if (!clip) return;
  drawSrc(); drawPreview(); updateHead(); updatePvPlayer();
  $("#timeLabel").textContent = `${fmtMs(video.currentTime)} / ${fmt(dur())}`;
  const cur = currentSegment();
  if (cur !== lastActiveSeg) {
    lastActiveSeg = cur;
    $("#segTable tbody").querySelectorAll("tr").forEach((tr) => tr.classList.toggle("active", tr._seg === cur));
    $("#pvSegs").querySelectorAll(".pvSeg").forEach((el) => el.classList.toggle("on", el._seg === cur));
  }
}

// ---------- precise player under the preview (scoped to the selected cut) ----------
function fmtMs(sec) { return fmtUnits(sec, 3); } // m:ss.mmm (h:mm:ss.mmm when long)
function pvRange() { const c = sel(); return c ? [c.start, c.end] : [0, dur()]; }
function barTimeAt(x, width, a, b) { // pure: bar pixel → time, clamped to [a, b]
  const f = width > 0 ? Math.max(0, Math.min(1, x / width)) : 0;
  return a + f * (b - a);
}
// pure: new {start, end} for a caption dragged by dt seconds.
// mode: "move" keeps the duration; "start"/"end" resize one edge. lo/hi = walls, minDur = shortest.
function dragSegment(orig, mode, dt, lim) {
  const { lo, hi, minDur } = lim;
  let { start, end } = orig;
  if (mode === "move") {
    const d = end - start;
    start = Math.max(lo, Math.min(hi - d, start + dt));
    end = start + d;
  } else if (mode === "start") {
    start = Math.max(lo, Math.min(end - minDur, start + dt));
  } else {
    end = Math.min(hi, Math.max(start + minDur, end + dt));
  }
  return { start: +start.toFixed(3), end: +end.toFixed(3) };
}
// pure: magnet — snaps t to target when within tol seconds.
function snap(t, target, tol) { return Math.abs(t - target) <= tol ? target : t; }

// ---------- undo (Ctrl+Z) for caption edits ----------
const undoStack = [];
function pushUndo() {
  undoStack.push(segments.map((s) => ({ ...s })));
  if (undoStack.length > 20) undoStack.shift();
}
function undo() {
  if (!undoStack.length) { toast(T("undo.none")); return; }
  segments = undoStack.pop();
  changed(true);
  toast(T("undo.done"));
}

const SEG_MIN_DUR = 0.15, SNAP_PX = 6, EDGE_PX = 7;
let segDrag = null;
function segWalls(s) { // neighbours (and the cut) are walls a caption can't cross
  const [a, b] = pvRange();
  const sorted = [...segments].sort((x, y) => x.start - y.start);
  const i = sorted.indexOf(s);
  const prev = sorted[i - 1], next = sorted[i + 1];
  return {
    lo: Math.max(a, prev ? Math.min(prev.end, s.start) : -Infinity),
    hi: Math.min(b, next ? Math.max(next.start, s.end) : Infinity),
    minDur: SEG_MIN_DUR,
  };
}
function placeSegEl(el, s, a, b) {
  el.style.left = `${(Math.max(s.start, a) - a) / (b - a) * 100}%`;
  el.style.width = `${Math.max(0.6, (Math.min(s.end, b) - Math.max(s.start, a)) / (b - a) * 100)}%`;
  el.title = `${fmtMs(s.start - a)} – ${fmtMs(s.end - a)}: ${s.text}\n${T("seg.barTitle")}`;
}
function segModeAt(el, clientX) {
  const r = el.getBoundingClientRect();
  const x = clientX - r.left;
  const edge = r.width < EDGE_PX * 3 ? r.width / 3 : EDGE_PX;
  return x <= edge ? "start" : x >= r.width - edge ? "end" : "move";
}
function renderPvSegs() {
  const box = $("#pvSegs");
  box.innerHTML = "";
  if (!clip) return;
  const [a, b] = pvRange();
  if (b <= a) return;
  for (const s of segments) {
    if (s.end <= a || s.start >= b || !s.text.trim()) continue;
    const el = document.createElement("div");
    el.className = "pvSeg";
    el._seg = s;
    placeSegEl(el, s, a, b);
    el.addEventListener("pointerdown", (e) => {
      e.stopPropagation();
      el.setPointerCapture(e.pointerId);
      segDrag = { el, s, mode: segModeAt(el, e.clientX), x0: e.clientX, orig: { start: s.start, end: s.end },
                  walls: segWalls(s), moved: false,
                  head: video.currentTime }; // magnet target = playhead when the drag began
    });
    el.addEventListener("pointermove", (e) => {
      if (!segDrag || segDrag.el !== el) {
        const m = segModeAt(el, e.clientX);
        el.style.cursor = m === "move" ? "grab" : "ew-resize";
        return;
      }
      const d = segDrag;
      if (!d.moved && Math.abs(e.clientX - d.x0) < 3) return; // still a click
      if (!d.moved) { d.moved = true; pushUndo(); el.classList.add("drag"); video.pause(); }
      const r = $("#pvBar").getBoundingClientRect();
      const secPerPx = (b - a) / r.width;
      let dt = (e.clientX - d.x0) * secPerPx;
      // Magnet: the dragged edge (or, when moving, either edge) sticks to the playhead.
      const head = d.head, tol = SNAP_PX * secPerPx;
      if (d.mode !== "end" && snap(d.orig.start + dt, head, tol) === head) dt = head - d.orig.start;
      else if (d.mode !== "start" && snap(d.orig.end + dt, head, tol) === head) dt = head - d.orig.end;
      const nv = dragSegment(d.orig, d.mode, dt, d.walls);
      s.start = nv.start; s.end = nv.end;
      placeSegEl(el, s, a, b);
      const hv = $("#pvHover");
      const edgeT = d.mode === "end" ? s.end : s.start;
      const x = (edgeT - a) / (b - a) * r.width;
      hv.classList.remove("hidden");
      hv.classList.toggle("flip", x > r.width * 0.6);
      hv.style.left = `${x}px`;
      hv.innerHTML = `<span>${fmtMs(s.start - a)} – ${fmtMs(s.end - a)}</span>`;
      // Show the frame at the edge being placed (start edge when moving).
      const target = d.mode === "end" ? s.end - 0.001 : s.start + 0.001;
      if (Math.abs(video.currentTime - target) > frame() / 2 && d.mode !== "move") seek(target);
      else drawAll();
    });
    el.addEventListener("pointerup", () => {
      const d = segDrag;
      segDrag = null;
      if (!d || d.el !== el) return;
      el.classList.remove("drag");
      $("#pvHover").classList.add("hidden");
      if (d.moved) { changed(true); return; }
      seek(s.start + 0.001); // plain click → jump there and reveal its table row
      const tr = [...$("#segTable tbody").children].find((row) => row._seg === s);
      if (tr) { tr.scrollIntoView({ block: "nearest", behavior: "smooth" }); tr.classList.add("flash"); setTimeout(() => tr.classList.remove("flash"), 900); }
    });
    box.appendChild(el);
  }
  lastActiveSeg = undefined; // force highlight refresh
}
function updatePvPlayer() {
  const [a, b] = pvRange();
  const t = video.currentTime;
  $("#pvHead").style.left = `${b > a ? Math.max(0, Math.min(1, (t - a) / (b - a))) * 100 : 0}%`;
  if (document.activeElement !== $("#pvTime")) $("#pvTime").value = fmtMs(Math.max(0, t - a));
  $("#pvDur").textContent = `/ ${fmtMs(b - a)}`;
  $("#pvA").textContent = sel() ? T("pv.clipStart") : fmt(a);
  $("#pvB").textContent = sel() ? fmtMs(b - a) : fmt(b);
}
let pvDrag = false;
function pvSeekFromEvent(e) {
  const r = $("#pvBar").getBoundingClientRect();
  const [a, b] = pvRange();
  seek(barTimeAt(e.clientX - r.left, r.width, a, b));
}
$("#pvBar").addEventListener("pointerdown", (e) => {
  if (!clip) return;
  pvDrag = true;
  $("#pvBar").setPointerCapture(e.pointerId);
  pvSeekFromEvent(e);
});
$("#pvBar").addEventListener("pointermove", (e) => {
  if (!clip || segDrag) return; // a caption drag owns the balloon
  const r = $("#pvBar").getBoundingClientRect();
  const [a, b] = pvRange();
  const x = Math.max(0, Math.min(r.width, e.clientX - r.left));
  const hv = $("#pvHover");
  hv.classList.remove("hidden");
  hv.classList.toggle("flip", x > r.width * 0.7);
  hv.style.left = `${x}px`;
  hv.innerHTML = `<span>${fmtMs(barTimeAt(x, r.width, a, b) - a)}</span>`;
  if (pvDrag) pvSeekFromEvent(e);
});
$("#pvBar").addEventListener("pointerup", () => { pvDrag = false; });
$("#pvBar").addEventListener("pointerleave", () => { if (!pvDrag) $("#pvHover").classList.add("hidden"); });

// Play from wherever the playhead is; with a cut selected, stop at the cut's end.
function togglePlay() {
  if (!clip) return;
  if (!video.paused) { video.pause(); return; }
  const c = sel();
  if (c) {
    const t = video.currentTime;
    if (t < c.start - 0.01 || t >= c.end - 0.03) seek(c.start);
    playUntil = c.end;
  } else playUntil = null;
  video.play();
}
function stepFrames(n) { video.pause(); seek(video.currentTime + n * frame()); }
$("#pvPlay").onclick = togglePlay;
$("#pvStart").onclick = () => seek(pvRange()[0]);
$("#pvBack1").onclick = () => seek(video.currentTime - 1);
$("#pvFwd1").onclick = () => seek(video.currentTime + 1);
$("#pvPrevF").onclick = () => stepFrames(-1);
$("#pvNextF").onclick = () => stepFrames(1);
$("#pvRate").onchange = (e) => { video.playbackRate = +e.target.value; };
$("#pvTime").addEventListener("keydown", (e) => {
  if (e.key !== "Enter") return;
  const v = parseT(e.target.value);
  if (isNaN(v)) { toast(T("time.invalidHint"), true); return; }
  const [a, b] = pvRange();
  seek(Math.min(b, a + v));
  e.target.blur();
});
$("#pvTime").addEventListener("focus", (e) => e.target.select());
function loop() {
  if (playUntil != null && video.currentTime >= playUntil) { video.pause(); playUntil = null; }
  drawAll();
  if (!video.paused) requestAnimationFrame(loop);
}

// ---------- player ----------
video.addEventListener("loadeddata", () => { video.playbackRate = +$("#pvRate").value; renderTimeline(); renderPvSegs(); drawAll(); });
video.addEventListener("seeked", drawAll);
video.addEventListener("play", () => { $("#playBtn").textContent = $("#pvPlay").textContent = "❚❚"; requestAnimationFrame(loop); });
video.addEventListener("pause", () => { $("#playBtn").textContent = $("#pvPlay").textContent = "▶"; drawAll(); });
$("#playBtn").onclick = togglePlay;
function seek(t) { video.currentTime = Math.max(0, Math.min(dur(), t)); }
const frame = () => 1 / (clip?.video?.fps || 30);

document.addEventListener("keydown", (e) => {
  // Ctrl+Z: undo caption edits (inside text fields the browser's own undo keeps working).
  if (clip && (e.ctrlKey || e.metaKey) && !e.shiftKey && e.key.toLowerCase() === "z"
      && !e.target.matches("input, select, textarea")) {
    e.preventDefault(); undo(); return;
  }
  if (!clip || e.target.matches("input, select, textarea") || e.ctrlKey || e.metaKey || e.altKey) return;
  const k = e.key.toLowerCase();
  if (e.code === "Space") { e.preventDefault(); togglePlay(); }
  else if (e.code === "ArrowLeft") seek(video.currentTime - (e.shiftKey ? 5 : 1));
  else if (e.code === "ArrowRight") seek(video.currentTime + (e.shiftKey ? 5 : 1));
  else if (e.code === "Home") { e.preventDefault(); seek(pvRange()[0]); }
  else if (k === ",") stepFrames(-1);
  else if (k === ".") stepFrames(1);
  else if (k === "i") $("#markIn").click();
  else if (k === "o") $("#markOut").click();
  else if (k === "n") $("#addCut").click();
});

// ---------- timeline (with zoom around the playhead) ----------
let view = { a: 0, b: 1 };
function computeView() {
  const d = dur() || 1;
  const span = d * Math.pow(Math.min(1, 20 / d), +$("#zoom").value); // zoom 1 → ~20 s window
  let a = video.currentTime - span / 2;
  a = Math.max(0, Math.min(d - span, a));
  view = { a, b: a + span };
}
const pct = (t) => ((t - view.a) / (view.b - view.a)) * 100;
function renderTimeline() {
  if (!clip) return;
  computeView();
  const box = $("#tlCuts");
  box.innerHTML = "";
  for (const c of cuts) {
    if (c.end < view.a || c.start > view.b) continue;
    const d = document.createElement("div");
    d.className = `tlCut${c.id === selId ? " sel" : ""}${c.output ? " done" : ""}`;
    d.style.left = `${Math.max(0, pct(c.start))}%`;
    d.style.width = `${Math.max(0.4, pct(Math.min(c.end, view.b)) - Math.max(0, pct(c.start)))}%`;
    d.textContent = c.name;
    d.title = `${c.name}: ${fmt(c.start)} – ${fmt(c.end)}`;
    d.onclick = (e) => { e.stopPropagation(); selectCut(c.id, true); };
    box.appendChild(d);
  }
  const r = $("#tlRange");
  if (inPt != null || outPt != null) {
    const a = inPt ?? video.currentTime, b = outPt ?? video.currentTime;
    r.classList.remove("hidden");
    r.style.left = `${pct(Math.min(a, b))}%`;
    r.style.width = `${Math.max(0.2, pct(Math.max(a, b)) - pct(Math.min(a, b)))}%`;
  } else r.classList.add("hidden");
  $("#tlStart").textContent = fmt(view.a);
  $("#tlEnd").textContent = fmt(view.b);
  $("#inOutLabel").textContent = inPt != null || outPt != null
    ? `${inPt != null ? fmt(inPt) : "—"} → ${outPt != null ? fmt(outPt) : "—"}` : "";
}
function updateHead() {
  const old = view;
  computeView();
  if (Math.abs(old.a - view.a) > (view.b - view.a) * 0.002 || Math.abs(old.b - view.b) > 0.01) renderTimeline();
  $("#tlHead").style.left = `${pct(video.currentTime)}%`;
}
$("#zoom").addEventListener("input", renderTimeline);
let tlDrag = false;
function tlSeek(e) {
  const rect = $("#timeline").getBoundingClientRect();
  const f = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
  seek(view.a + f * (view.b - view.a));
}
$("#timeline").addEventListener("pointerdown", (e) => { if (e.target.closest(".tlCut")) return; tlDrag = true; $("#timeline").setPointerCapture(e.pointerId); playUntil = null; tlSeek(e); });
$("#timeline").addEventListener("pointermove", (e) => { if (tlDrag) tlSeek(e); });
$("#timeline").addEventListener("pointerup", () => { tlDrag = false; });

// ---------- cuts ----------
$("#markIn").onclick = () => { inPt = +video.currentTime.toFixed(2); if (outPt != null && outPt <= inPt) outPt = null; renderTimeline(); };
$("#markOut").onclick = () => { outPt = +video.currentTime.toFixed(2); if (inPt != null && inPt >= outPt) inPt = null; renderTimeline(); };
function newCutId() { return Math.random().toString(16).slice(2, 10).padEnd(8, "0"); }
$("#addCut").onclick = () => {
  if (!clip) return;
  let a = inPt ?? video.currentTime, b = outPt ?? Math.min(dur(), a + 30);
  if (b - a < 0.5) { toast(T("cut.tooShortMark"), true); return; }
  // Number after "Clip N" / "Klip N" (names may have been created in either UI language).
  const n = cuts.reduce((m, c) => Math.max(m, +(/^(?:Clip|Klip) (\d+)$/.exec(c.name)?.[1] || 0)), 0) + 1;
  const c = { id: newCutId(), name: T("cut.defaultName", { n }), start: +a.toFixed(2), end: +b.toFixed(2), regions: null, modes: null, output: null };
  cuts.push(c);
  cuts.sort((x, y) => x.start - y.start);
  inPt = outPt = null;
  selectCut(c.id, false);
  saveState();
  toast(T("cut.added", { name: c.name, len: fmt(c.end - c.start) }));
};
function selectCut(id, jump) {
  selId = id;
  const c = sel();
  if (c && jump) { playUntil = null; seek(c.start + 0.01); }
  refreshAll();
}

function statusHtml(c) {
  const st = cutStatus[c.id] || {};
  const parts = [];
  for (const kind of ["render", "transcribe"]) {
    const s = st[kind];
    if (!s) continue;
    if (s.status === "running") parts.push(`${kind === "render" ? "🎬" : "💬"} ${esc(s.message)}<div class="mini"><div style="width:${Math.round(s.progress * 100)}%"></div></div>`);
    else if (s.status === "error") parts.push(`<span title="${esc(s.message)}">⚠ ${esc(s.message).slice(0, 80)}</span>`);
  }
  if (c.output) {
    const u = `/output/${encodeURIComponent(c.output)}`;
    parts.push(`✓ <a href="${u}" target="_blank">${esc(T("cut.open"))}</a><a href="${u}?dl=1">${esc(T("cut.download"))}</a>`);
  }
  return parts.join("<br>") || "—";
}
function renderCutTable() {
  const tb = $("#cutTable tbody");
  tb.innerHTML = "";
  for (const c of cuts) {
    const tr = document.createElement("tr");
    tr.className = c.id === selId ? "sel" : "";
    const segN = segments.filter((s) => s.end > c.start && s.start < c.end).length;
    const err = Object.values(cutStatus[c.id] || {}).some((s) => s.status === "error");
    tr.innerHTML = `
      <td><button class="play" title="${esc(T("common.play"))}">▶</button></td>
      <td><input class="name"></td>
      <td><input class="tm st"> <button class="setA" title="${esc(T("cut.setStart"))}">⇤</button></td>
      <td><input class="tm en"> <button class="setB" title="${esc(T("cut.setEnd"))}">⇥</button></td>
      <td class="mono">${fmt(c.end - c.start)}</td>
      <td><span class="badge ${c.regions ? "own" : ""}">${esc(T(c.regions ? "cut.own" : "cut.shared"))}</span> <span class="badge">${esc(T("cut.captions", { n: segN }))}</span></td>
      <td class="st ${err ? "err" : ""}">${statusHtml(c)}</td>
      <td><button class="tr" title="${esc(T("cut.whisperTitle"))}">💬</button> <button class="rn" title="${esc(T("cut.renderTitle"))}">🎬</button> <button class="del" title="${esc(T("common.delete"))}">✕</button></td>`;
    const q = (s) => tr.querySelector(s);
    q("input.name").value = c.name;
    q("input.st").value = fmt(c.start);
    q("input.en").value = fmt(c.end);
    tr.onclick = (e) => { if (!e.target.closest("input, button, a")) selectCut(c.id, true); };
    q(".play").onclick = () => playCut(c);
    q("input.name").onchange = (e) => { c.name = e.target.value.trim() || c.name; renderTimeline(); saveState(); };
    const setTime = (key, val) => {
      if (isNaN(val)) { toast(T("time.invalid"), true); return renderCutTable(); }
      const t = Math.max(0, Math.min(dur(), val));
      if (key === "start" ? t >= c.end - 0.5 : t <= c.start + 0.5) { toast(T("cut.minLen"), true); return renderCutTable(); }
      c[key] = +t.toFixed(2);
      cuts.sort((x, y) => x.start - y.start);
      refreshAll(); saveState();
    };
    q("input.st").onchange = (e) => setTime("start", parseT(e.target.value));
    q("input.en").onchange = (e) => setTime("end", parseT(e.target.value));
    q(".setA").onclick = () => setTime("start", video.currentTime);
    q(".setB").onclick = () => setTime("end", video.currentTime);
    q(".tr").onclick = () => startTranscribe(c);
    q(".rn").onclick = () => startRender(c);
    q(".del").onclick = () => {
      if (!confirm(T("cut.confirmDelete", { name: c.name }))) return;
      cuts = cuts.filter((x) => x !== c);
      if (selId === c.id) selId = cuts[0]?.id ?? null;
      refreshAll(); saveState();
    };
    tb.appendChild(tr);
  }
  $("#cutEmpty").classList.toggle("hidden", cuts.length > 0);
  $("#cutTable").classList.toggle("hidden", cuts.length === 0);
  $("#cutCount").textContent = cuts.length ? T("cuts.count", { n: cuts.length, done: cuts.filter((c) => c.output).length }) : "";
  const c = sel();
  $("#pvCutName").textContent = c ? `· ${c.name} (${fmt(c.end - c.start)})` : "";
  $("#renderBtn").disabled = !c; $("#transcribeBtn").disabled = !c;
  $("#addSeg").disabled = !c; $("#clearSegs").disabled = !c;
  $("#shiftBack").disabled = !c; $("#shiftFwd").disabled = !c;
}
function playCut(c) { // table row ▶: select the cut and play from the playhead (or its start)
  if (selId !== c.id) { selId = c.id; refreshAll(); }
  video.pause();
  togglePlay();
}

// ---------- jobs per cut ----------
async function watchCutJob(kind, cutId, jobId) {
  cutStatus[cutId] = cutStatus[cutId] || {};
  const update = (j) => {
    cutStatus[cutId][kind] = { status: j.status, progress: j.progress, message: j.status === "error" ? (j.error || j.message) : j.message };
    const row = [...$("#cutTable tbody").children][cuts.findIndex((c) => c.id === cutId)];
    const c = cuts.find((x) => x.id === cutId);
    if (row && c) { const td = row.querySelector("td.st"); td.innerHTML = statusHtml(c); td.classList.toggle("err", j.status === "error"); }
    if (cutId === selId) {
      const el = kind === "render" ? $("#renderProgress") : $("#trProgress");
      setProgress(el, j.progress, j.status === "error" ? (j.error || j.message) : j.message, j.status === "error");
    }
  };
  try {
    const r = await pollJob(jobId, update);
    delete cutStatus[cutId][kind];
    const c = cuts.find((x) => x.id === cutId);
    if (kind === "render" && c) { c.output = r.output; toast(T("cut.ready", { name: c.name })); }
    if (kind === "transcribe") {
      // Server merged the new captions into the source timeline; keep local edits elsewhere.
      const a = c ? c.start : -Infinity, b = c ? c.end : Infinity;
      const fresh = r.segments.filter((s) => s.end > a && s.start < b);
      pushUndo(); // whisper replaced this cut's captions → Ctrl+Z brings the old ones back
      segments = segments.filter((s) => s.end <= a || s.start >= b).concat(fresh).sort((x, y) => x.start - y.start);
      saveState(); // an autosave during the job may have sent the old list — push the merged one
      if (cutId === selId) setProgress($("#trProgress"), 1, T("w.done", { n: r.added }));
    }
    if (kind === "render" && cutId === selId && c) setProgress($("#renderProgress"), 1, T("render.ready", { file: c.output }));
    refreshAll(); loadRecent();
  } catch (err) {
    refreshAll();
  }
}
async function startTranscribe(c) {
  const has = segments.some((s) => s.end > c.start && s.start < c.end);
  if (has && !confirm(T("w.confirmReplace", { name: c.name }))) return;
  try {
    const r = await api(`/api/clips/${clip.id}/cuts/${c.id}/transcribe`, {
      method: "POST",
      body: { ...statePayload(), language: $("#lang").value, model: $("#wModel").value, vad: $("#wVad").checked, prompt: $("#wPrompt").value },
    });
    watchCutJob("transcribe", c.id, r.job_id);
  } catch (err) { toast(err.message, true); }
}
async function startRender(c) {
  try {
    const r = await api(`/api/clips/${clip.id}/cuts/${c.id}/render`, { method: "POST", body: statePayload() });
    watchCutJob("render", c.id, r.job_id);
  } catch (err) { toast(err.message, true); }
}
$("#renderBtn").onclick = () => { const c = sel(); if (c) startRender(c); };
$("#transcribeBtn").onclick = () => { const c = sel(); if (c) startTranscribe(c); };
$("#renderAll").onclick = async () => {
  if (!clip) return;
  try {
    const r = await api(`/api/clips/${clip.id}/render-all`, { method: "POST", body: statePayload() });
    for (const [cutId, jobId] of Object.entries(r.jobs)) watchCutJob("render", cutId, jobId);
    toast(T("render.queued", { n: Object.keys(r.jobs).length }));
  } catch (err) { toast(err.message, true); }
};
$("#openFolder").onclick = (e) => { e.preventDefault(); api("/api/open-output", { method: "POST", body: {} }); };

// ---------- subtitles (only the selected cut's range; stored in source time) ----------
function renderSegTable() {
  const tb = $("#segTable tbody");
  tb.innerHTML = "";
  const c = sel();
  const base = c ? c.start : 0;
  const shown = c ? segments.filter((s) => s.end > c.start && s.start < c.end) : [];
  for (const s of shown) {
    const tr = document.createElement("tr");
    tr._seg = s;
    tr.innerHTML = `
      <td><button title="${esc(T("seg.goTitle"))}">▶</button></td>
      <td><input class="tm" type="number" step="0.05"></td>
      <td><input class="tm" type="number" step="0.05"></td>
      <td class="grow"><input class="txt"></td>
      <td><button title="${esc(T("cut.setStart"))}">⇤</button> <button title="${esc(T("common.delete"))}">✕</button></td>`;
    const [go, setNow, del] = tr.querySelectorAll("button");
    const [st, en, tx] = tr.querySelectorAll("input");
    st.value = (s.start - base).toFixed(2); en.value = (s.end - base).toFixed(2); tx.value = s.text;
    go.onclick = () => { playUntil = null; seek(s.start + 0.01); };
    setNow.onclick = () => { pushUndo(); s.start = +video.currentTime.toFixed(2); changed(true); };
    del.onclick = () => { pushUndo(); segments.splice(segments.indexOf(s), 1); changed(true); };
    st.onchange = () => { pushUndo(); s.start = Math.max(0, +(+st.value + base).toFixed(2)); changed(true); };
    en.onchange = () => { pushUndo(); s.end = Math.max(s.start + 0.05, +(+en.value + base).toFixed(2)); changed(true); };
    let typed = false; // one undo step per editing session of a line
    tx.oninput = () => { if (!typed) { pushUndo(); typed = true; } s.text = tx.value; changed(false); };
    tx.onfocus = () => { typed = false; if (video.paused) seek(s.start + 0.01); };
    tb.appendChild(tr);
  }
  $("#segScope").textContent = c ? T("seg.scope", { name: c.name }) : T("seg.noCut");
  $("#segEmpty").classList.toggle("hidden", shown.length > 0);
  $("#segTable").classList.toggle("hidden", shown.length === 0);
  lastActiveSeg = null;
}
function changed(rerender) {
  if (rerender) { segments.sort((a, b) => a.start - b.start); renderSegTable(); renderCutTable(); }
  renderPvSegs(); drawAll(); saveState();
}
$("#addSeg").onclick = () => {
  const c = sel();
  if (!c) return;
  const t = +Math.max(c.start, Math.min(video.currentTime, c.end - 0.3)).toFixed(2);
  const next = segments.find((s) => s.start > t);
  const end = Math.min(t + 1.5, next ? next.start : Infinity, c.end);
  const s = { start: t, end: +Math.max(t + 0.3, end).toFixed(2), text: "" };
  pushUndo();
  segments.push(s);
  changed(true);
  [...$("#segTable tbody").children].find((tr) => tr._seg === s)?.querySelector("input.txt")?.focus();
};
function shiftCutSegments(delta) {
  const c = sel();
  if (!c) return;
  const inside = segments.filter((s) => s.end > c.start && s.start < c.end);
  if (!inside.length) return;
  pushUndo();
  for (const s of inside) {
    s.start = +Math.max(0, s.start + delta).toFixed(2);
    s.end = +Math.max(s.start + 0.05, s.end + delta).toFixed(2);
  }
  changed(true);
  toast(T("seg.shifted", { d: `${delta > 0 ? "+" : ""}${delta.toFixed(1)}` }));
}
$("#shiftBack").onclick = () => shiftCutSegments(-0.1);
$("#shiftFwd").onclick = () => shiftCutSegments(0.1);
$("#savePrompt").onclick = async () => {
  if (!clip) return;
  await api("/api/presets", { method: "POST", body: { channel: clip.channel, preset: { prompt: $("#wPrompt").value } } });
  toast(T("prompt.saved", { channel: clip.channel }));
};

$("#clearSegs").onclick = () => {
  const c = sel();
  if (!c) return;
  const inside = segments.filter((s) => s.end > c.start && s.start < c.end);
  if (inside.length && confirm(T("seg.confirmClear", { name: c.name, n: inside.length }))) {
    pushUndo(); segments = segments.filter((s) => !inside.includes(s)); changed(true);
  }
};

// style inputs
const styleFields = [
  ["#sFont", "font", "text"], ["#sSize", "size", "num"], ["#sColor", "color", "text"],
  ["#sOutlineColor", "outline_color", "text"], ["#sOutline", "outline", "num"],
  ["#sOffset", "offset_y", "num"], ["#sUpper", "uppercase", "bool"], ["#sWords", "max_words", "num"],
];
function fillStyleInputs() {
  for (const [s, key, t] of styleFields) {
    const el = $(s);
    if (t === "bool") el.checked = !!style[key]; else el.value = style[key] ?? "";
  }
}
for (const [s, key, t] of styleFields) {
  $(s).addEventListener("input", (e) => {
    style[key] = t === "bool" ? e.target.checked : t === "num" ? +e.target.value : e.target.value;
    drawAll(); saveState();
  });
}
$("#saveStyle").onclick = async () => {
  await api("/api/style", { method: "POST", body: style });
  defaultStyle = { ...style };
  toast(T("style.saved"));
};

// ---------- UI language ----------
$("#uiLang").value = LANG;
$("#uiLang").onchange = (e) => {
  setUiLang(e.target.value);
  loadStatus().catch(() => {}); // model labels come from the server in the UI language
  loadRecent().catch(() => {});
  updateSizeHint();
  if (clip) refreshAll();
};

// ---------- init ----------
(async () => {
  applyI18n();
  try { await loadStatus(); } catch (e) { toast(T("server.unreachable"), true); }
  updateSizeHint();
  loadRecent();
  const m = location.hash.match(/clip=([a-f0-9]{12})/);
  if (m) openClip(m[1]).catch(() => {});
})();
