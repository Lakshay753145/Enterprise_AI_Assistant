"""Small display helpers shared by the API and CLI scripts."""

from __future__ import annotations


def format_bytes(size: int | float) -> str:
    """1536 -> '1.5 KB'."""
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} GB"


def format_duration(milliseconds: float) -> str:
    """850 -> '850 ms'; 4200 -> '4.2 s'; 65000 -> '1m 5s'."""
    if milliseconds < 1000:
        return f"{milliseconds:.0f} ms"
    seconds = milliseconds / 1000
    if seconds < 60:
        return f"{seconds:.1f} s"
    minutes, seconds = divmod(int(seconds), 60)
    return f"{minutes}m {seconds}s"


def truncate(text: str, length: int = 120, suffix: str = "...") -> str:
    collapsed = " ".join(text.split())
    if len(collapsed) <= length:
        return collapsed
    return collapsed[: length - len(suffix)].rsplit(" ", 1)[0] + suffix
