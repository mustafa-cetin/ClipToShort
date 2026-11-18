"""User-facing server messages in English (default) and Turkish.

The UI sends its language in the `X-Lang` header; `app.py` stores it in a context variable for the
request, and `jobs.start` copies the context into the job thread, so `t()` anywhere (request handler,
background job) answers in the language of the request that started the work.
Transcription language is a separate setting (`whisper_language`) and is not affected by this.
"""
from contextvars import ContextVar

LANGS = ("en", "tr")
DEFAULT_LANG = "en"
_lang: ContextVar[str] = ContextVar("lang", default=DEFAULT_LANG)

MESSAGES: dict[str, dict[str, str]] = {
    # request validation
    "bad_url": {"en": "Enter a valid Kick VOD link",
                "tr": "Geçerli bir Kick VOD linki girin"},
    "range_too_long": {"en": "The range can be at most {hours} hours",
                       "tr": "Aralık en fazla {hours} saat olabilir"},
    "download_first": {"en": "Download the video first",
                       "tr": "Önce video indirilmeli"},
    "cut_not_found": {"en": "Clip not found",
                      "tr": "Klip bulunamadı"},
    "cut_not_found_deleted": {"en": "Clip not found (it may have been deleted)",
                              "tr": "Klip bulunamadı (silinmiş olabilir)"},
    "select_regions": {"en": "Select the camera and game regions",
                       "tr": "Kamera ve oyun bölgelerini seçin"},
    "cut_too_short": {"en": "“{name}” is too short",
                      "tr": "“{name}” çok kısa"},
    "add_cut_first": {"en": "Add at least one clip first",
                      "tr": "Önce en az bir klip ekleyin"},
    "missing_data": {"en": "Missing data",
                     "tr": "Eksik veri"},
    "bad_clip_id": {"en": "Invalid clip id",
                    "tr": "Geçersiz klip id"},
    # time parsing
    "time_empty": {"en": "Time is empty",
                   "tr": "Zaman boş"},
    "time_invalid": {"en": "Invalid time: {text}",
                     "tr": "Geçersiz zaman: {text}"},
    "end_before_start": {"en": "End time must be after the start time",
                         "tr": "Bitiş zamanı başlangıçtan sonra olmalı"},
    # jobs
    "job_starting": {"en": "Starting…", "tr": "Başlıyor…"},
    "job_done": {"en": "Done", "tr": "Tamamlandı"},
    "job_error": {"en": "Error", "tr": "Hata"},
    "program_not_found": {"en": "Program not found: {cmd}",
                          "tr": "Program bulunamadı: {cmd}"},
    "program_failed": {"en": "{name} failed: {detail}",
                       "tr": "{name} hata verdi: {detail}"},
    # download
    "meta_failed": {"en": "Could not get video info. Is the link correct?",
                    "tr": "Video bilgisi alınamadı. Link doğru mu?"},
    "no_video_stream": {"en": "The downloaded file has no video",
                        "tr": "İndirilen dosyada video yok"},
    "converting": {"en": "Converting to a browser-compatible format…",
                   "tr": "Tarayıcı uyumlu formata çevriliyor…"},
    "convert_failed": {"en": "Could not convert the video: {detail}",
                       "tr": "Video dönüştürülemedi: {detail}"},
    "fetching_meta": {"en": "Getting video info…", "tr": "Video bilgisi alınıyor…"},
    "start_after_end": {"en": "Start ({start}) is after the end of the video ({duration})",
                        "tr": "Başlangıç ({start}) videonun süresinden ({duration}) sonra"},
    "vod_duration_unknown": {"en": "VOD length unknown; enter an end time",
                             "tr": "VOD süresi bilinmiyor; bitiş zamanını girin"},
    "downloading_range": {"en": "Downloading: {start} → {end}", "tr": "İndiriliyor: {start} → {end}"},
    "downloading_pct": {"en": "Downloading… {pct}%", "tr": "İndiriliyor… %{pct}"},
    "downloading_time": {"en": "Downloading… {t} / {total}", "tr": "İndiriliyor… {t} / {total}"},
    "download_failed": {"en": "Download failed: {detail}", "tr": "İndirme başarısız: {detail}"},
    "probing": {"en": "Inspecting the video…", "tr": "Video inceleniyor…"},
    "unknown_channel": {"en": "unknown", "tr": "bilinmeyen"},
    # render
    "unsafe_path": {"en": "Path is not usable in a filter: {path}",
                    "tr": "Yol filtre için uygun değil: {path}"},
    "render_queued": {"en": "Queued… ({n} render(s) ahead)",
                      "tr": "Sırada… (önünde {n} render var)"},
    "rendering_pct": {"en": "Rendering… {pct}%", "tr": "Render ediliyor… %{pct}"},
    "render_starting": {"en": "Starting render ({encoder})…", "tr": "Render başlıyor ({encoder})…"},
    "render_failed": {"en": "Render failed: {detail}", "tr": "Render başarısız: {detail}"},
    "clip_default_name": {"en": "Clip {n}", "tr": "Klip {n}"},
    # transcription
    "whisper_queued": {"en": "Queued… (starts when another caption job finishes)",
                       "tr": "Sırada… (başka bir altyazı işi bitince başlayacak)"},
    "whisper_missing": {"en": "whisper.cpp not found. Run scripts\\setup_whisper.ps1 first.",
                        "tr": "whisper.cpp bulunamadı. Önce scripts\\setup_whisper.ps1 çalıştırın."},
    "extracting_audio": {"en": "Extracting audio…", "tr": "Ses çıkarılıyor…"},
    "audio_failed": {"en": "Could not extract audio: {detail}", "tr": "Ses çıkarılamadı: {detail}"},
    "finding_speech": {"en": "Finding speech (VAD)…", "tr": "Konuşma bölümleri bulunuyor (VAD)…"},
    "no_speech": {"en": "No speech found in this clip (only music/game audio).",
                  "tr": "Bu klipte konuşma bulunamadı (sadece müzik/oyun sesi)."},
    "transcribing": {"en": "Transcribing ({model})…", "tr": "Altyazı çıkarılıyor ({model})…"},
    "transcribing_pct": {"en": "Transcribing ({model})… {pct}%", "tr": "Altyazı çıkarılıyor ({model})… %{pct}"},
    "whisper_failed": {"en": "whisper failed: {detail}", "tr": "whisper hata verdi: {detail}"},
    # model labels
    "model_large_v3": {"en": "large-v3 (most accurate)", "tr": "large-v3 (en doğru)"},
    "model_large_v3_turbo": {"en": "large-v3-turbo (fast)", "tr": "large-v3-turbo (hızlı)"},
}


def normalize_lang(value: str | None) -> str:
    """'tr', 'tr-TR', 'TR' → 'tr'; anything unknown → DEFAULT_LANG."""
    code = (value or "").strip().lower()[:2]
    return code if code in LANGS else DEFAULT_LANG


def set_lang(value: str | None) -> str:
    lang = normalize_lang(value)
    _lang.set(lang)
    return lang


def get_lang() -> str:
    return _lang.get()


def t(key: str, **params) -> str:
    """Message `key` in the current language, formatted with params (falls back to English, then the key)."""
    entry = MESSAGES.get(key)
    if entry is None:
        return key
    text = entry.get(get_lang()) or entry[DEFAULT_LANG]
    return text.format(**params) if params else text
