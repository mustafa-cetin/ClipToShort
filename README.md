# Kick → TikTok Clip

A fully **local** tool that turns Kick stream recordings (VODs) into vertical **1080×1920**
TikTok/Reels/Shorts clips: streamer camera on top, gameplay below, and automatic (offline) captions on
the line where the two meet.

The interface is in English by default and can be switched to Turkish from the top-right corner.
Captions are tuned for **Turkish** speech (the default transcription language); English and automatic
language detection are also available.

## Features

- **Source download:** only the range you need is downloaded from the VOD (no re-encoding, fast). Empty start/end = start/end of the VOD.
- **Many clips from one source:** mark as many clips as you like on the timeline with **I** / **O** / **N**, render them one by one or all in sequence.
- **Region selection:** draw the camera and game areas with the mouse; *blurred fill* or *fill (crop)*. Regions are shared, or specific to one clip when needed. Saved per streamer.
- **Adjustable layout:** camera/game ratio 20 %–80 % (slider, or drag the seam in the preview).
- **Live 9:16 preview** and a millisecond-accurate player bar (single-frame steps, 0.25×/0.5× speed).
- **Offline captions:** whisper.cpp (large-v3 / turbo), speech-only sections via Silero VAD, word timing via DTW, hallucination filter, per-streamer hint text.
- **Caption editing:** table + drag blocks on the player bar to adjust timing (magnet, Ctrl+Z).
- **Render:** FFmpeg; NVIDIA NVENC is used automatically when available, otherwise libx264.

## Requirements

| | |
|---|---|
| Operating system | Windows 10/11 (other systems need `scripts/setup_whisper.ps1` and the font copy adapted) |
| Python | 3.10+ |
| [FFmpeg](https://ffmpeg.org/) | `ffmpeg` and `ffprobe` on PATH (a build with libass, e.g. gyan.dev "essentials") |
| [yt-dlp](https://github.com/yt-dlp/yt-dlp) | on PATH |
| GPU (optional) | NVIDIA + CUDA 12 compatible driver → faster whisper and render |
| Disk | whisper models ~4.7 GB; 1080p60 source ~2.7 GB/hour |

## Installation

```bat
git clone <this-repo>
cd tiktok-video-maker
powershell -ExecutionPolicy Bypass -File scripts\setup_whisper.ps1
start.bat
```

- `setup_whisper.ps1` downloads the CUDA and CPU builds of whisper.cpp, `ggml-large-v3.bin` (most accurate,
  ~3.1 GB), `ggml-large-v3-turbo.bin` (fast, ~1.6 GB) and the Silero VAD model into `tools\whisper\`.
  To get only one model: `-Models ggml-large-v3-turbo.bin`.
- On first run `start.bat` creates `.venv` and installs the dependencies, then opens
  `http://127.0.0.1:5000` in the browser. Port: `PORT` environment variable.

## Usage

1. **Download a source:** VOD link + range (`1:23:10`, `83:10`, `4990`, `1h23m10s`; end `+30m` = 30 min).
2. **Regions:** pick *Camera* / *Game* and draw a rectangle on the video; set the fit mode and layout ratio.
3. **Clips:** in the player, **I** sets the in point, **O** the out point, **N** adds a clip.
4. **Captions:** with a clip selected, *Whisper for this clip*; fix the result in the table or by dragging on the bar.
5. **Render:** *Render this clip* or *Render all* → `output\<channel>_<VOD-time>_<clip>.mp4`.

**Shortcuts:** Space play/pause · ←/→ 1 s (Shift 5 s) · `,` / `.` one frame · Home clip start ·
I / O / N · Ctrl+Z undo caption change.

**Interface language:** English / Türkçe selector in the header (remembered in the browser). Server messages
(progress, errors) follow the same language. The caption *Language* setting is separate and defaults to Turkish.

## Settings (`config.json`, optional)

Create `config.json` in the project root to change the defaults (the file is not committed):

```json
{
  "ffmpeg": "C:/ffmpeg/bin/ffmpeg.exe",
  "ffprobe": "C:/ffmpeg/bin/ffprobe.exe",
  "ytdlp": "yt-dlp",
  "whisper_model": "ggml-large-v3.bin",
  "whisper_language": "tr",
  "whisper_vad": true,
  "whisper_dtw": true,
  "whisper_filter": true,
  "max_source_seconds": 14400
}
```

The caption style (`style`) is saved with the *Make default* button in the UI. Streamer settings are kept
in `presets.json`.

## How caption quality is achieved

- **Model:** large-v3 by default (noticeably more accurate than turbo for Turkish).
- **VAD:** Silero finds the speech sections and only those are fed to whisper, back to back; times are mapped
  back to the original video. (whisper-cli's own `--vad` option is not used because it doesn't map DTW times back.)
- **DTW** token timestamps → words land on the moment they are spoken.
- **Hint text** (`--prompt`); specially encoded on Windows so Turkish characters reach whisper-cli intact.
- Audio pre-processing, repetition-loop removal and a filter for known hallucination phrases ("Altyazı M.K." etc.).

**Known limitation:** the game's own voice-over is on the same audio track as the streamer, so it may also be
detected as speech. Those lines can be deleted from the table or the bar.

## Development

```bat
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements-dev.txt
.venv\Scripts\python -m pytest
```

| Path | Contents |
|---|---|
| `app.py` | Flask server and API |
| `core/downloader.py` | range download with yt-dlp, ffprobe |
| `core/render.py` | FFmpeg filtergraph, cut render, render queue |
| `core/transcribe.py` | whisper.cpp, VAD, DTW, hallucination filter |
| `core/subtitles.py` | ASS caption generation |
| `core/clips.py`, `core/jobs.py` | source/clip state (`work/<id>/clip.json`), background jobs |
| `core/i18n.py` | server messages (English / Turkish) |
| `static/` | UI (dependency-free HTML/CSS/JS); UI strings in `static/i18n.js` |
| `scripts/setup_whisper.ps1` | installs whisper.cpp and the models |

See [CONTRIBUTING.md](CONTRIBUTING.md) to contribute.

## Third-party components

This repository does **not** include the following; the user installs them or the setup script downloads them.

| Component | License |
|---|---|
| [FFmpeg](https://ffmpeg.org/legal.html) | LGPL/GPL (depending on the build) |
| [yt-dlp](https://github.com/yt-dlp/yt-dlp) | Unlicense |
| [whisper.cpp](https://github.com/ggml-org/whisper.cpp) | MIT |
| [OpenAI Whisper model weights](https://github.com/openai/whisper) | MIT |
| [Silero VAD](https://github.com/snakers4/silero-vad) | MIT |
| [Flask](https://flask.palletsprojects.com/) | BSD-3-Clause |
| Arial Black font | Owned by Microsoft — not added to the repo, copied from the local Windows installation |

## Legal notice

This tool only downloads and edits on your own computer. **You are responsible** for the rights to the
content you download and share, for following the terms of the platforms involved (Kick, TikTok, etc.), and
for getting the streamers' permission. Ask for permission before sharing someone else's streams.

## License

[MIT](LICENSE) © 2026 tiktok-video-maker contributors
