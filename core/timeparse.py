"""Parse user-entered timestamps into seconds."""
import re

_HMS = re.compile(r"^\s*(?:(\d+)h)?\s*(?:(\d+)m)?\s*(?:(\d+(?:[.,]\d+)?)s)?\s*$", re.I)


def parse_time(text: str) -> float:
    """Accepts '1:23:10', '83:10', '4990', '4990.5', '1h23m10s', '23m', '45s'."""
    if text is None:
        raise ValueError("Zaman boş")
    s = str(text).strip().replace(",", ".")
    if not s:
        raise ValueError("Zaman boş")
    if ":" in s:
        parts = s.split(":")
        if len(parts) > 3 or any(p.strip() == "" for p in parts):
            raise ValueError(f"Geçersiz zaman: {text}")
        try:
            nums = [float(p) for p in parts]
        except ValueError:
            raise ValueError(f"Geçersiz zaman: {text}") from None
        total = 0.0
        for n in nums:
            total = total * 60 + n
        if any(n < 0 for n in nums) or any(n >= 60 for n in nums[1:]):
            raise ValueError(f"Geçersiz zaman: {text}")
        return total
    try:
        v = float(s)
        if v < 0:
            raise ValueError(f"Geçersiz zaman: {text}")
        return v
    except ValueError:
        pass
    m = _HMS.match(s)
    if m and any(m.groups()):
        h, mi, se = m.groups()
        return int(h or 0) * 3600 + int(mi or 0) * 60 + float(se or 0)
    raise ValueError(f"Geçersiz zaman: {text}")


def parse_range(start: str, end: str | None) -> tuple[float, float | None]:
    """End may be absolute, a '+duration' relative to start, or empty (→ None = VOD end).
    Empty start = 0."""
    a = parse_time(start) if str(start or "").strip() else 0.0
    end = str(end or "").strip()
    if not end:
        return a, None
    b = a + parse_time(end[1:]) if end.startswith("+") else parse_time(end)
    if b <= a:
        raise ValueError("Bitiş zamanı başlangıçtan sonra olmalı")
    return a, b


def fmt_time(sec: float, decimals: int = 0) -> str:
    sec = max(0.0, sec)
    h = int(sec // 3600)
    m = int(sec % 3600 // 60)
    s = sec - h * 3600 - m * 60
    s_str = f"{s:0{3 + decimals}.{decimals}f}" if decimals else f"{int(s):02d}"
    return f"{h:02d}:{m:02d}:{s_str}"
