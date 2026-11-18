# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Local, offline Windows tool: downloads a range of a Kick VOD (yt-dlp), lets the user mark many clips in a
browser UI, and renders vertical 1080×1920 videos with FFmpeg — streamer camera on top, gameplay below,
burned-in captions (whisper.cpp) on the seam. Flask backend, dependency-free HTML/CSS/JS frontend. The user
communicates in Turkish. Code, comments and docs are English. The UI is English by default and switchable to
Turkish; most videos are Turkish, so transcription defaults to `whisper_language: "tr"` and the Turkish-specific
transcription bits (hallucination phrases, `tr_upper`, `utf8_argv`, Turkish test transcripts) stay Turkish.

**i18n.** UI strings live in `static/i18n.js` (`I18N.en` / `I18N.tr`, `T(key, params)`; markup uses
`data-i18n`, `data-i18n-html`, `data-i18n-title`, `data-i18n-placeholder`). Every key must exist in both
languages. `api()` sends the UI language as `X-Lang`; `app.py` sets it per request (`core/i18n.py`, a
ContextVar) and `jobs.start` copies the context into the job thread, so `t(key, **params)` in jobs answers in
the language of the request that started them. Add server messages to `core/i18n.MESSAGES` (en + tr).

## Commands

```bat
start.bat                                   :: creates .venv on first run, starts app, opens browser
set PORT=5055 && .venv\Scripts\python app.py --no-browser   :: run on another port
.venv\Scripts\python -m pip install -r requirements-dev.txt
.venv\Scripts\python -m pytest                               :: all tests
.venv\Scripts\python -m pytest tests/test_core.py::test_clamp_split   :: single test
node --check static/app.js                                   :: JS syntax check (no build/lint step)
powershell -ExecutionPolicy Bypass -File scripts\setup_whisper.ps1    :: whisper.cpp + models → tools\whisper\
```

- The user usually has their own server running on port 5000 (`start.bat`). For end-to-end checks start a
  second instance with `PORT=5055` and drive it through the HTTP API; stop it afterwards. Code changes only
  take effect after the server is restarted.
- The Windows console is cp1252: run Python scripts that print Turkish text with `-X utf8`
  (`-I` ignores `PYTHONIOENCODING`).
- There is no browser automation here; UI changes can only be checked with `node --check`, an ID
  cross-check between `static/app.js` and `static/index.html`, and by extracting pure JS helpers
  (`barTimeAt`, `fmtUnits`, `dragSegment`, `snap`, …) into a Node script. Say so when UI wasn't clicked through.

## Architecture

**Data model.** A *source* is one downloaded range = one directory `work/<12-hex id>/` containing
`source.mp4` and `clip.json` (all editor state). In code it is still called `clip` (`core/clips.py`).
A source holds shared `regions` / `modes` / `split`, a list of `cuts` (the actual TikTok clips; each may
override `regions`/`modes`/`split`), and **one caption list `segments` on the source timeline** (seconds
from the start of `source.mp4`). Cuts are sliced out of it at render time (`render.segments_for_cut`);
the UI shows times relative to the selected cut but stores source time. `render.effective_layout` resolves
cut overrides → shared values.

**Concurrency.** Long work (download, transcribe, render) runs as background jobs (`core/jobs.py`); the
frontend polls `/api/jobs/<id>`. Jobs and the UI's debounced autosave (`PUT /api/clips/<id>/state`) both
write `clip.json`, so all read-modify-write goes through `clips.mutate()` (locked). Render outputs are owned
by the server: `_apply_state` in `app.py` keeps each cut's `output` from disk and ignores the client's copy.
Renders are serialized by a FIFO lock whose ticket is taken in the request handler (`render.take_ticket()`)
so "render all" finishes in click order; whisper is serialized by its own lock (GPU memory).

**Render** (`core/render.py`): one filtergraph per cut written to `work/<id>/filter_<cutid>.txt`; ffmpeg runs
with `cwd=work/<id>` so the `ass=` / `fontsdir=` paths are relative (drive-letter colons would need escaping —
`_ffpath` refuses unsafe paths). Input seeking `-ss/-to` before `-i` makes cuts frame-accurate even though
the source was downloaded with stream copy. Camera section height = `split` (384–1536, even); the ASS
caption `\pos` y follows it. NVENC is probed with a real encode at startup and falls back to libx264.

**Transcription** (`core/transcribe.py`) — pipeline per cut, each step fixes a real failure seen in testing:
1. ffmpeg extracts the cut's audio (highpass + dynaudnorm).
2. `whisper-vad-speech-segments.exe` (Silero) finds speech; **only speech chunks are concatenated** into the
   wav fed to whisper (`build_speech_track`), and times are mapped back with `remap_time`. Do not switch to
   whisper-cli's `--vad`: it leaves DTW token times on the compressed timeline. Do not just mute non-speech:
   large-v3 then outputs only the "Altyazı M.K." hallucination when a window starts with silence.
3. `whisper-cli -ojf -dtw <preset> -nfa -sns -mc 0` — DTW only works with flash attention off (`-nfa`);
   without DTW, per-token offsets just spread a segment's duration over its words (seconds off).
4. `words_from_tokens` builds words from tokens (`t_dtw` is in centiseconds), drops known hallucination
   phrases / low-confidence fragments, `collapse_repeats` removes loops, `fit_to_speech` clamps to VAD
   intervals, `words_to_segments` groups into short captions, `merge_segments` replaces only the cut's range.
- whisper-cli reads ANSI argv on Windows: Turkish characters in `--prompt` must go through `utf8_argv`
  (UTF-8 bytes pre-decoded with the ANSI code page).

**Download** (`core/downloader.py`): `yt-dlp --download-sections` with stream copy (no
`--force-keyframes-at-cuts`; that re-encodes into MPEG-TS defaults — mpeg2video/mp3 — which browsers can't
show). The section starts at the preceding keyframe (a few seconds early); `make_browser_playable`
transcodes only if the codec isn't H.264.

**Frontend** (`static/app.js`, single file, no framework): one hidden `<video>` is the source for both
canvases — the region editor (`drawSrc`) and the 9:16 preview (`drawPreview`), which re-implements the
ffmpeg graph (blur/cover, split, caption wrap) so it's WYSIWYG; keep both in sync when changing rendering.
`layout()` returns the selected cut's effective regions/modes/split. `refreshAll()` re-renders tables,
timeline, player-bar caption blocks and preview; caption edits call `pushUndo()` before mutating.

## Gotchas

- This FFmpeg build removed `-filter_complex_script`; use `-/filter_complex <file>`.
- `jobs.run_process` keeps only the last ~60 output lines (fine for progress); use `jobs.run_simple` when the
  full stdout must be parsed (e.g. VAD output).
- `tools/`, `work/`, `output/`, `config.json`, `presets.json` and `assets/fonts/*` are user data / large
  downloads and are git-ignored. Arial Black (`assets/fonts/ariblk.ttf`) is proprietary and copied from
  `C:\Windows\Fonts` at startup — never commit fonts.
- `work/` contains the user's real sources and captions: test on throwaway sources (or restore anything
  changed) rather than editing theirs.
- Commits follow Conventional Commits (`feat(render): …`, `fix(transcribe): …`), see CONTRIBUTING.md.
