# Kick → TikTok Klip

Kick yayın kayıtlarından (VOD) dikey **1080×1920** TikTok/Reels/Shorts klipleri hazırlayan, tamamen
**yerelde** çalışan bir araç. Üstte yayıncı kamerası, altta oyun görüntüsü, ikisinin birleştiği çizgide
otomatik (çevrimdışı) altyazı.

> **English:** A local, offline tool that turns Kick VODs into vertical 1080×1920 clips — streamer cam on
> top, gameplay below, burned-in captions on the seam. Python + Flask UI in the browser, FFmpeg for
> rendering, yt-dlp for downloading, whisper.cpp (offline) for Turkish/English captions. Windows-first.

## Özellikler

- **Kaynak indirme:** VOD'un sadece istediğin aralığı iner (yeniden kodlamadan, hızlı). Boş başlangıç/bitiş = VOD'un başı/sonu.
- **Bir kaynaktan çok klip:** zaman çizelgesinde **I** / **O** / **N** ile istediğin kadar klip işaretle, tek tek ya da hepsini sırayla render et.
- **Bölge seçimi:** kamera ve oyun alanını fareyle çiz; *bulanık dolgu* ya da *doldur (kırp)*. Bölgeler ortak, gerekirse klibe özel. Yayıncı başına kaydedilir.
- **Ayarlanabilir düzen:** kamera/oyun oranı %20–%80 (kaydırıcı ya da önizlemede çizgiyi sürükleyerek).
- **Canlı 9:16 önizleme** ve milisaniye hassasiyetinde oynatıcı çubuğu (tek kare adım, 0,25×/0,5× hız).
- **Çevrimdışı altyazı:** whisper.cpp (large-v3 / turbo), Silero VAD ile sadece konuşma bölümleri, DTW ile kelime zamanlaması, halüsinasyon filtresi, yayıncı başına ipucu metni.
- **Altyazı düzenleme:** tablo + oynatıcı çubuğunda blokları sürükleyerek zamanlama (mıknatıs, Ctrl+Z).
- **Render:** FFmpeg; NVIDIA NVENC varsa otomatik kullanılır, yoksa libx264.

## Gereksinimler

| | |
|---|---|
| İşletim sistemi | Windows 10/11 (başka sistemler için `scripts/setup_whisper.ps1` ve font kopyalama uyarlanmalı) |
| Python | 3.10+ |
| [FFmpeg](https://ffmpeg.org/) | `ffmpeg` ve `ffprobe` PATH'te (libass içeren bir derleme, ör. gyan.dev "essentials") |
| [yt-dlp](https://github.com/yt-dlp/yt-dlp) | PATH'te |
| GPU (opsiyonel) | NVIDIA + CUDA 12 uyumlu sürücü → whisper ve render hızlanır |
| Disk | whisper modelleri ~4,7 GB; 1080p60 kaynak ~2,7 GB/saat |

## Kurulum

```bat
git clone <bu-depo>
cd tiktok-video-maker
powershell -ExecutionPolicy Bypass -File scripts\setup_whisper.ps1
start.bat
```

- `setup_whisper.ps1`, whisper.cpp'nin CUDA ve CPU sürümlerini, `ggml-large-v3.bin` (en doğru, ~3,1 GB),
  `ggml-large-v3-turbo.bin` (hızlı, ~1,6 GB) ve Silero VAD modelini `tools\whisper\` altına indirir.
  Sadece birini istersen: `-Models ggml-large-v3-turbo.bin`.
- `start.bat` ilk çalıştırmada `.venv` oluşturup bağımlılıkları kurar, ardından tarayıcıda
  `http://127.0.0.1:5000` adresini açar. Port: `PORT` ortam değişkeni.

## Kullanım

1. **Kaynak indir:** VOD linki + aralık (`1:23:10`, `83:10`, `4990`, `1h23m10s`; bitiş `+30m` = 30 dk).
2. **Bölgeler:** *Kamera* / *Oyun* seçip videonun üzerinde dikdörtgen çiz; sığdırma modunu ve düzen oranını ayarla.
3. **Klipler:** oynatıcıda **I** giriş, **O** çıkış, **N** klip ekle.
4. **Altyazı:** klip seçiliyken *Bu klip için Whisper*; tablodan ya da çubukta sürükleyerek düzelt.
5. **Render:** *Render bu klip* veya *Tümünü render et* → `output\<kanal>_<VOD-saati>_<klip>.mp4`.

**Kısayollar:** Boşluk oynat/durdur · ←/→ 1 sn (Shift 5 sn) · `,` / `.` tek kare · Home klip başı ·
I / O / N · Ctrl+Z altyazı değişikliğini geri al.

## Ayarlar (`config.json`, opsiyonel)

Proje kökünde `config.json` oluşturarak varsayılanları değiştirebilirsin (dosya git'e eklenmez):

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

Altyazı stili (`style`) arayüzdeki *Varsayılan yap* düğmesiyle kaydedilir. Yayıncı ayarları `presets.json`'da tutulur.

## Altyazı kalitesi nasıl sağlanıyor

- **Model:** large-v3 varsayılan (Türkçede turbo'dan belirgin şekilde doğru).
- **VAD:** Silero ile konuşma bölümleri bulunur, whisper'a yalnızca onlar art arda verilir; zamanlar orijinal
  videoya geri çevrilir. (whisper-cli'nin kendi `--vad` seçeneği DTW zamanlarını geri çevirmediği için kullanılmaz.)
- **DTW** token zamanları → kelimeler konuşulduğu ana oturur.
- **İpucu metni** (`--prompt`); Windows'ta Türkçe karakterlerin whisper-cli'ye bozulmadan ulaşması için özel kodlanır.
- Ses ön işleme, tekrar döngüsü ve bilinen halüsinasyon cümlesi ("Altyazı M.K." vb.) filtresi.

**Bilinen sınır:** oyunun kendi seslendirmesi yayıncının sesiyle aynı kanalda olduğundan, onu da konuşma olarak
algılayabilir. Bu satırlar tablodan ya da çubuktan silinebilir.

## Geliştirme

```bat
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements-dev.txt
.venv\Scripts\python -m pytest
```

| Yol | İçerik |
|---|---|
| `app.py` | Flask sunucusu ve API |
| `core/downloader.py` | yt-dlp ile aralık indirme, ffprobe |
| `core/render.py` | FFmpeg filtre grafiği, kesim render'ı, render kuyruğu |
| `core/transcribe.py` | whisper.cpp, VAD, DTW, halüsinasyon filtresi |
| `core/subtitles.py` | ASS altyazı üretimi |
| `core/clips.py`, `core/jobs.py` | kaynak/klip durumu (`work/<id>/clip.json`), arka plan işleri |
| `static/` | arayüz (bağımlılıksız HTML/CSS/JS) |
| `scripts/setup_whisper.ps1` | whisper.cpp ve modellerin kurulumu |

Katkı için bkz. [CONTRIBUTING.md](CONTRIBUTING.md).

## Üçüncü taraf bileşenler

Bu depo aşağıdakileri **içermez**; kullanıcı kendisi kurar ya da kurulum script'i indirir.

| Bileşen | Lisans |
|---|---|
| [FFmpeg](https://ffmpeg.org/legal.html) | LGPL/GPL (derlemeye göre) |
| [yt-dlp](https://github.com/yt-dlp/yt-dlp) | Unlicense |
| [whisper.cpp](https://github.com/ggml-org/whisper.cpp) | MIT |
| [OpenAI Whisper model ağırlıkları](https://github.com/openai/whisper) | MIT |
| [Silero VAD](https://github.com/snakers4/silero-vad) | MIT |
| [Flask](https://flask.palletsprojects.com/) | BSD-3-Clause |
| Arial Black fontu | Microsoft'a ait — depoya eklenmez, yerel Windows kurulumundan kopyalanır |

## Yasal not

Bu araç yalnızca indirme ve düzenleme işlemini yerel bilgisayarında yapar. İndirdiğin ve paylaştığın
içeriklerin haklarından, ilgili platformların (Kick, TikTok vb.) kullanım koşullarına uymaktan ve
yayıncıların izinlerinden **kullanıcı sorumludur**. Başkasına ait yayınları paylaşmadan önce izin al.

## Lisans

[MIT](LICENSE) © 2026 tiktok-video-maker contributors
