# Katkıda bulunma

Katkıların için teşekkürler! Hata bildirimi, öneri ve pull request'lere açığız.

## Hata bildirirken

- Windows sürümü, Python sürümü, `ffmpeg -version` ilk satırı, `yt-dlp --version`, GPU modeli.
- Ne yaptın, ne bekliyordun, ne oldu? Arayüzdeki hata mesajını ve sunucu konsolundaki çıktıyı ekle.
- Kişisel bilgi, çerez ya da indirilen yayın dosyalarını **paylaşma**.

## Geliştirme ortamı

```bat
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements-dev.txt
.venv\Scripts\python -m pytest
set PORT=5055 && .venv\Scripts\python app.py
```

## Pull request kuralları

- Değişiklik başına bir konu; küçük ve okunabilir PR'lar.
- Yeni mantık için `tests/` altına test ekle; `pytest` geçmeli.
- Arayüz değişikliklerinde `node --check static/app.js` hatasız olmalı; mümkünse ekran görüntüsü ekle.
- Commit mesajları [Conventional Commits](https://www.conventionalcommits.org/) biçiminde: `feat(render): …`, `fix(transcribe): …`.
- Depoya model, araç, video, ses ya da tescilli font dosyası **ekleme** (`.gitignore` bunları dışlar).

## Lisans

Gönderdiğin katkılar projenin [MIT lisansı](LICENSE) altında yayımlanır.
