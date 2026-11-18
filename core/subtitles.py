"""Captions → ASS file positioned on the seam (y=960) of the 1080x1920 frame."""

W, H, SEAM = 1080, 1920, 960


def tr_upper(text: str) -> str:
    return text.replace("i", "İ").replace("ı", "I").upper()


def ass_color(hex_color: str, alpha: int = 0) -> str:
    h = hex_color.lstrip("#")
    if len(h) != 6:
        h = "FFFFFF"
    r, g, b = h[0:2], h[2:4], h[4:6]
    return f"&H{alpha:02X}{b}{g}{r}".upper()


def ass_time(sec: float) -> str:
    cs = max(0, round(sec * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def escape(text: str) -> str:
    text = text.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}")
    return text.replace("\r", "").replace("\n", "\\N")


def build_ass(segments: list[dict], style: dict, seam_y: int = SEAM) -> str:
    """Captions centred on the camera/game seam (seam_y = camera section height)."""
    font = style.get("font", "Arial Black")
    size = int(style.get("size", 72))
    outline = float(style.get("outline", 6))
    shadow = float(style.get("shadow", 2))
    y = int(seam_y) + int(style.get("offset_y", 0))
    primary = ass_color(style.get("color", "#FFFFFF"))
    outline_c = ass_color(style.get("outline_color", "#000000"))
    back = ass_color("#000000", 0x80)

    lines = [
        "[Script Info]",
        "ScriptType: v4.00+",
        f"PlayResX: {W}",
        f"PlayResY: {H}",
        "WrapStyle: 0",
        "ScaledBorderAndShadow: yes",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
        "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
        "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: Default,{font},{size},{primary},{primary},{outline_c},{back},"
        f"-1,0,0,0,100,100,0,0,1,{outline:g},{shadow:g},5,60,60,0,1",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    for seg in sorted(segments, key=lambda s: s["start"]):
        text = (seg.get("text") or "").strip()
        if not text or seg["end"] <= seg["start"]:
            continue
        if style.get("uppercase"):
            text = tr_upper(text)
        lines.append(f"Dialogue: 0,{ass_time(seg['start'])},{ass_time(seg['end'])},Default,,0,0,0,,"
                     f"{{\\an5\\pos({W // 2},{y})}}{escape(text)}")
    return "\n".join(lines) + "\n"
