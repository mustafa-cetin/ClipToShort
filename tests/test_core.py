import pytest

from core.render import (build_filtergraph, build_render_cmd, clamp_region, effective_layout,
                         segments_for_cut)
from core.subtitles import ass_color, ass_time, build_ass, tr_upper
from core.timeparse import fmt_time, parse_range, parse_time
from core.transcribe import merge_segments, parse_whisper_json, words_to_segments


@pytest.mark.parametrize("text,expected", [
    ("1:23:10", 4990), ("83:10", 4990), ("4990", 4990), ("4990,5", 4990.5),
    ("1h23m10s", 4990), ("23m", 1380), ("45s", 45), ("0:00:05.25", 5.25),
])
def test_parse_time(text, expected):
    assert parse_time(text) == pytest.approx(expected)


@pytest.mark.parametrize("bad", ["", "abc", "1:99", "1::2", "-5"])
def test_parse_time_invalid(bad):
    with pytest.raises(ValueError):
        parse_time(bad)


def test_parse_range():
    assert parse_range("1:00", "1:30") == (60, 90)
    assert parse_range("1:00", "+45") == (60, 105)
    assert parse_range("1:00", "") == (60, None)      # empty end → VOD end
    assert parse_range("", "") == (0, None)
    with pytest.raises(ValueError):
        parse_range("2:00", "1:00")


def test_segments_for_cut_shift_and_clip():
    segs = [{"start": 5, "end": 9, "text": "önce"},       # ends before cut
            {"start": 9, "end": 11, "text": "kenar"},     # straddles cut start
            {"start": 12, "end": 13, "text": "içinde"},
            {"start": 19, "end": 25, "text": "taşan"},    # straddles cut end
            {"start": 21, "end": 22, "text": "sonra"},
            {"start": 14, "end": 15, "text": "  "}]       # empty text dropped
    out = segments_for_cut(segs, 10, 20)
    assert out == [{"start": 0, "end": 1, "text": "kenar"},
                   {"start": 2, "end": 3, "text": "içinde"},
                   {"start": 9, "end": 10, "text": "taşan"}]


def test_merge_segments_replaces_only_range():
    old = [{"start": 1, "end": 2, "text": "a"}, {"start": 11, "end": 12, "text": "b"},
           {"start": 25, "end": 26, "text": "c"}]
    new = [{"start": 10.5, "end": 11.5, "text": "yeni"}]
    assert [s["text"] for s in merge_segments(old, new, 10, 20)] == ["a", "yeni", "c"]


def test_effective_layout_override():
    clip = {"regions": {"cam": {"x": 1}, "game": {"x": 2}}, "modes": {"cam": "blur", "game": "cover"}}
    assert effective_layout(clip, {"regions": None, "modes": None}) == (clip["regions"], clip["modes"], 960)
    own = {"cam": {"x": 9}, "game": {"x": 8}}
    regions, modes, split = effective_layout(clip, {"regions": own, "modes": {"cam": "cover", "game": "bad"}})
    assert regions == own and modes == {"cam": "cover", "game": "blur"} and split == 960
    # split: shared value, overridden per cut
    clip["split"] = 700
    assert effective_layout(clip, {"split": None})[2] == 700
    assert effective_layout(clip, {"split": 641})[2] == 640


def test_render_cmd_seeks_input():
    cmd = build_render_cmd("ffmpeg", "source.mp4", 12.5, 42, "filter_x.txt", ["-c:v", "libx264"], True, "o.mp4")
    i = cmd.index("-i")
    assert cmd[i - 4:i] == ["-ss", "12.500", "-to", "42.000"]
    assert "-/filter_complex" in cmd and cmd[-1] == "o.mp4" and "0:a:0?" in cmd


def test_fifo_lock_order():
    import threading
    import time
    from core.render import _FifoLock
    lock, order = _FifoLock(), []
    tickets = [lock.take_ticket() for _ in range(4)]

    def worker(i):
        lock.acquire(tickets[i])
        order.append(i)
        lock.release()
    threads = [threading.Thread(target=worker, args=(i,)) for i in (3, 1, 2, 0)]  # start out of order
    for t in threads:
        t.start()
        time.sleep(0.01)
    for t in threads:
        t.join(2)
    assert order == [0, 1, 2, 3]


def test_fmt_time():
    assert fmt_time(4990) == "01:23:10"


def test_clamp_region_even_and_inside():
    r = clamp_region({"x": 1901, "y": -5, "w": 500, "h": 301}, 1920, 1080)
    assert r["x"] % 2 == 0 and r["w"] % 2 == 0 and r["h"] % 2 == 0
    assert r["x"] + r["w"] <= 1920 and r["y"] == 0


def test_filtergraph_blur_and_subs():
    cam = {"x": 10, "y": 20, "w": 400, "h": 300}
    game = {"x": 0, "y": 0, "w": 1920, "h": 1080}
    g = build_filtergraph(cam, game, "blur", "cover", "subs.ass", "../../assets/fonts")
    assert "[0:v]crop=400:300:10:20,split=2[topa][topb]" in g
    assert "gblur" in g and "[topbg][topfg]overlay" in g
    assert "[0:v]crop=1920:1080:0:0,scale=1080:960:force_original_aspect_ratio=increase,crop=1080:960" in g
    assert g.endswith("[top][bot]vstack=inputs=2,format=yuv420p,ass=subs.ass:fontsdir=../../assets/fonts[v]")


def test_filtergraph_no_subs_fps_cap():
    r = {"x": 0, "y": 0, "w": 100, "h": 100}
    g = build_filtergraph(r, r, "cover", "cover", max_fps=60)
    assert "ass=" not in g and "fps=60" in g


def test_whisper_words_and_grouping():
    data = {"transcription": [
        {"offsets": {"from": 0, "to": 300}, "text": " merhaba"},
        {"offsets": {"from": 300, "to": 600}, "text": " arkadaşlar"},
        {"offsets": {"from": 600, "to": 620}, "text": "!"},
        {"offsets": {"from": 700, "to": 900}, "text": " bugün"},
        {"offsets": {"from": 900, "to": 1100}, "text": " çok"},
        {"offsets": {"from": 1100, "to": 1400}, "text": " güzel"},
        {"offsets": {"from": 1400, "to": 1600}, "text": " bir"},
        {"offsets": {"from": 3000, "to": 3400}, "text": " oyun"},
    ]}
    words = parse_whisper_json(data)
    assert words[1]["text"] == "arkadaşlar!"
    segs = words_to_segments(words, max_words=3, max_chars=22)
    assert [s["text"] for s in segs] == ["merhaba arkadaşlar!", "bugün çok güzel", "bir", "oyun"]
    # gap of 0.08s bridged, gap of 1.4s kept
    assert segs[0]["end"] == segs[1]["start"]
    assert segs[2]["end"] < segs[3]["start"]


def test_zero_length_words_get_min_duration():
    words = [{"text": w, "start": 17.6, "end": 17.6} for w in "the body was said to be".split()]
    segs = words_to_segments(words, max_words=3)
    assert len(segs) == 2
    assert all(s["end"] - s["start"] >= 0.25 for s in segs)
    assert segs[1]["start"] >= segs[0]["end"]


def test_ass_output():
    ass = build_ass([{"start": 1.0, "end": 2.5, "text": "istanbul {x}"}],
                    {"size": 70, "uppercase": True, "offset_y": -10, "color": "#FF0000"})
    assert "PlayResY: 1920" in ass
    assert "Dialogue: 0,0:00:01.00,0:00:02.50,Default,,0,0,0,,{\\an5\\pos(540,950)}İSTANBUL \\{X\\}" in ass
    assert ass_color("#FF0000") == "&H000000FF"


def test_helpers():
    assert tr_upper("ılık iğne") == "ILIK İĞNE"
    assert ass_time(3725.456) == "1:02:05.46"


# ---- transcription quality ----
from core.transcribe import (build_whisper_cmd, collapse_repeats, dtw_preset, is_hallucination,
                             words_from_tokens)


def _tok(text, t_dtw, p=0.9, off=0):
    return {"text": text, "t_dtw": t_dtw, "p": p, "offsets": {"from": off, "to": off}}


def test_dtw_preset():
    assert dtw_preset("x/ggml-large-v3-turbo.bin") == "large.v3.turbo"
    assert dtw_preset("ggml-large-v3.bin") == "large.v3"
    assert dtw_preset("ggml-large-v3-q5_0.bin") == "large.v3"
    assert dtw_preset("ggml-medium.bin") == "medium"
    assert dtw_preset("custom.bin") is None


def test_words_from_tokens_uses_dtw_and_segment_text():
    # Shape taken from a real whisper-cli -ojf -dtw run.
    data = {"transcription": [{
        "text": " Bu ne pişiriyonuz lan?", "offsets": {"from": 0, "to": 16000},
        "tokens": [_tok("[_BEG_]", -1), _tok(" Bu", 1310), _tok(" ne", 1324), _tok(" piş", 1442),
                   _tok("ir", 1452), _tok("iy", 1458), _tok("on", 1468), _tok("uz", 1480),
                   _tok(" lan", 1490), _tok("?", 1568), _tok("[_TT_800]", -1)],
    }]}
    words = words_from_tokens(data)
    assert [w["text"] for w in words] == ["Bu", "ne", "pişiriyonuz", "lan?"]
    assert words[0]["start"] == 13.10            # DTW time, not the segment start (0.0)
    assert words[2]["start"] == 14.42 and words[2]["end"] == 14.90
    assert words[-1]["end"] <= 16.0


def test_words_from_tokens_fallback_offsets_and_utf8_fix():
    data = {"transcription": [{
        "text": " çok güzel", "offsets": {"from": 1000, "to": 2000},
        "tokens": [_tok(" \ufffd", -1, off=1000), _tok("ok", -1, off=1100), _tok(" güzel", -1, off=1500)],
    }]}
    words = words_from_tokens(data)
    assert [w["text"] for w in words] == ["çok", "güzel"]   # text repaired from segment text
    assert words[0]["start"] == 1.0 and words[1]["start"] == 1.5


def test_hallucination_and_low_confidence_dropped():
    data = {"transcription": [
        {"text": " Altyazı M.K.", "offsets": {"from": 0, "to": 2000}, "tokens": [_tok(" Altyazı", 10), _tok(" M.K.", 50)]},
        {"text": " hmm", "offsets": {"from": 3000, "to": 3500}, "tokens": [_tok(" hmm", 300, p=0.1)]},
        {"text": " hadi gel", "offsets": {"from": 5000, "to": 6000}, "tokens": [_tok(" hadi", 500), _tok(" gel", 560)]},
    ]}
    assert [w["text"] for w in words_from_tokens(data)] == ["hadi", "gel"]
    assert is_hallucination("İzlediğiniz için teşekkürler!")
    assert not is_hallucination("teşekkürler kanka çok iyiydi")


def test_collapse_repeats():
    w = lambda t, i: {"text": t, "start": i, "end": i + 0.5}
    loop = [w("Kırmaktadır.", i) for i in range(10)]
    assert len(collapse_repeats(loop)) == 1
    ok = [w("hadi", 0), w("hadi", 1), w("hadi", 2)]   # 3 in a row is natural speech
    assert len(collapse_repeats(ok)) == 3
    phrase = [w(t, i) for i, t in enumerate(["ne", "diyon", "lan"] * 5)]
    assert [x["text"] for x in collapse_repeats(phrase)] == ["ne", "diyon", "lan"]


def test_build_whisper_cmd():
    cmd = build_whisper_cmd("w.exe", "m.bin", "tr", vad_model="vad.bin", dtw="large.v3", prompt=" Hype, GTA ")
    for flag in ("-ojf", "-sns", "--vad", "-nfa"):
        assert flag in cmd
    assert cmd[cmd.index("-mc") + 1] == "0"
    assert cmd[cmd.index("-dtw") + 1] == "large.v3"
    assert cmd[cmd.index("--prompt") + 1] == "Hype, GTA"
    plain = build_whisper_cmd("w.exe", "m.bin", "tr", vad_model=None, dtw=None, prompt="")
    assert "--vad" not in plain and "-dtw" not in plain and "--prompt" not in plain


# ---- VAD gate + Windows argv ----
from core.transcribe import (build_speech_track, fit_to_speech, merge_intervals, parse_vad_output,
                             remap_time, utf8_argv)


def test_parse_and_merge_vad():
    out = "Detected 3 speech segments:\nSpeech segment 0: start = 208.00, end = 281.00\n" \
          "Speech segment 1: start = 300.00, end = 473.00\nSpeech segment 2: start = 909.00, end = 963.00\n"
    iv = parse_vad_output(out)
    assert iv == [(2.08, 2.81), (3.0, 4.73), (9.09, 9.63)]
    assert merge_intervals(iv, total=9.7) == [(1.88, 4.93), (8.89, 9.7)]   # padded, close ones merged


def test_fit_to_speech_drops_and_clamps():
    words = [{"text": "müzik", "start": 6.0, "end": 6.5},          # outside speech → dropped
             {"text": "merhaba", "start": 2.0, "end": 2.6},
             {"text": "son", "start": 4.5, "end": 6.0}]             # end clamped to interval end
    out = fit_to_speech(words, [(1.9, 5.0)])
    assert [w["text"] for w in out] == ["merhaba", "son"]
    assert out[1]["end"] == 5.0


def test_speech_track_and_remap(tmp_path):
    import array
    import wave
    p = tmp_path / "a.wav"
    with wave.open(str(p), "wb") as w:   # 10 s at 100 Hz; sample value = second index + 1
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(100)
        w.writeframes(array.array("h", [i // 100 + 1 for i in range(1000)]).tobytes())
    mapping = build_speech_track(p, [(2.0, 3.0), (7.0, 8.5)], gap=0.5)
    assert mapping == [(0.5, 1.5, 2.0), (2.0, 3.5, 7.0)]
    with wave.open(str(p)) as w:
        s = array.array("h", w.readframes(w.getnframes()))
    assert len(s) == 50 + 100 + 50 + 150 + 50
    assert set(s[:50]) == {0} and set(s[50:150]) == {3} and set(s[150:200]) == {0} and s[200] == 8
    assert remap_time(1.0, mapping) == 2.5          # inside chunk 1
    assert remap_time(2.5, mapping) == 7.5          # inside chunk 2
    assert remap_time(1.6, mapping) == 3.0          # in the pause → snaps to chunk 1 end
    assert remap_time(0.1, mapping) == 2.0          # leading pause → chunk 1 start


def test_utf8_argv_roundtrip_cp1252():
    text = "oğlum şifre ÇÖÜ İı"
    tricked = utf8_argv(text, 1252)
    assert tricked.encode("cp1252") == text.encode("utf-8")   # what the ANSI program receives
    assert utf8_argv(text, 65001) == text and utf8_argv(text, None) == text


# ---- adjustable camera / game split ----
from core.render import clamp_split


def test_clamp_split():
    assert clamp_split(960) == 960
    assert clamp_split(641) == 640            # even
    assert clamp_split(10) == 384             # 20 % minimum
    assert clamp_split(5000) == 1536          # 80 % maximum
    assert clamp_split(None) == 960 and clamp_split("x") == 960


def test_filtergraph_custom_split():
    r = {"x": 0, "y": 0, "w": 1920, "h": 1080}
    g = build_filtergraph(r, r, "blur", "cover", cam_h=640)
    assert "[topb]scale=1080:640:force_original_aspect_ratio=decrease" in g
    assert "gblur=sigma=10,scale=1080:640," in g and "scale=270:160:" in g      # quarter-size blur
    assert "[0:v]crop=1920:1080:0:0,scale=1080:1280:force_original_aspect_ratio=increase,crop=1080:1280" in g
    g2 = build_filtergraph(r, r, "blur", "blur", cam_h=1000)
    assert "scale=270:250:" in g2 and "scale=270:230:" in g2                       # 1000/4, 920/4 → even


def test_ass_follows_seam():
    ass = build_ass([{"start": 0, "end": 1, "text": "x"}], {"offset_y": -10}, seam_y=640)
    assert "\pos(540,630)" in ass


# --- i18n: server messages follow the UI language (X-Lang), English by default ---
from core import i18n, jobs


def test_i18n_lang_selection():
    assert i18n.normalize_lang("tr-TR") == "tr"
    assert i18n.normalize_lang(None) == "en"
    assert i18n.normalize_lang("de") == "en"
    assert set(i18n.MESSAGES["bad_url"]) == set(i18n.LANGS)
    for key, entry in i18n.MESSAGES.items():
        assert set(entry) == set(i18n.LANGS), key


def test_i18n_messages_in_request_language():
    import contextvars

    def in_lang(lang, fn):
        ctx = contextvars.copy_context()
        return ctx.run(lambda: (i18n.set_lang(lang), fn())[1])

    assert in_lang("en", lambda: i18n.t("cut_too_short", name="X")) == "“X” is too short"
    assert in_lang("tr", lambda: i18n.t("cut_too_short", name="X")) == "“X” çok kısa"
    with pytest.raises(ValueError, match="Geçersiz zaman"):
        in_lang("tr", lambda: parse_time("abc"))
    with pytest.raises(ValueError, match="Invalid time"):
        in_lang("en", lambda: parse_time("abc"))


def test_job_thread_inherits_language():
    import contextvars
    import time

    def start_in(lang):
        def go():
            i18n.set_lang(lang)
            return jobs.start("test", lambda rep: {"msg": i18n.t("job_done")})
        return contextvars.copy_context().run(go)

    ids = {lang: start_in(lang) for lang in ("en", "tr")}
    for _ in range(100):
        if all(jobs.get(j)["status"] == "done" for j in ids.values()):
            break
        time.sleep(0.02)
    assert jobs.get(ids["en"])["result"]["msg"] == "Done"
    assert jobs.get(ids["tr"])["result"]["msg"] == "Tamamlandı"
    assert jobs.get(ids["tr"])["message"] == "Tamamlandı"
