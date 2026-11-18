# Contributing

Thanks for contributing! Bug reports, suggestions and pull requests are welcome.

## Reporting a bug

- Windows version, Python version, first line of `ffmpeg -version`, `yt-dlp --version`, GPU model.
- What did you do, what did you expect, what happened? Include the error message from the UI and the
  output from the server console.
- **Do not share** personal information, cookies or downloaded stream files.

## Development environment

```bat
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements-dev.txt
.venv\Scripts\python -m pytest
set PORT=5055 && .venv\Scripts\python app.py
```

## Pull request guidelines

- One topic per change; small, readable PRs.
- Add tests under `tests/` for new logic; `pytest` must pass.
- For UI changes `node --check static/app.js` must pass; add a screenshot if possible.
- New UI strings go into `static/i18n.js` in **both** `en` and `tr`; new server messages into `core/i18n.py`.
- Commit messages follow [Conventional Commits](https://www.conventionalcommits.org/): `feat(render): …`, `fix(transcribe): …`.
- **Do not add** model, tool, video, audio or proprietary font files to the repo (`.gitignore` excludes them).

## License

Contributions you submit are published under the project's [MIT license](LICENSE).
