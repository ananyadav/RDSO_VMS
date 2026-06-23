"""Resolve FFmpeg executable path."""

import os
import shutil
from pathlib import Path


def ffmpeg_bin() -> str:
    env = (os.getenv("FFMPEG_PATH") or os.getenv("FFMPEG_BIN") or "").strip()
    if env:
        p = Path(env)
        if p.is_dir():
            exe = p / ("ffmpeg.exe" if os.name == "nt" else "ffmpeg")
            if exe.exists():
                return str(exe)
        if p.exists():
            return str(p)

    found = shutil.which("ffmpeg")
    if found:
        return found

    if os.name == "nt":
        candidates = [
            Path.home()
            / "Downloads"
            / "ffmpeg-2026-05-25-git-34dfa8bf2b-essentials_build"
            / "bin"
            / "ffmpeg.exe",
        ]
        for c in candidates:
            if c.exists():
                return str(c)

    return "ffmpeg"


def ffprobe_bin() -> str:
    """Resolve ffprobe next to ffmpeg or on PATH."""
    ffmpeg = ffmpeg_bin()
    p = Path(ffmpeg)
    if p.name.startswith("ffmpeg"):
        probe = p.parent / p.name.replace("ffmpeg", "ffprobe")
        if probe.exists():
            return str(probe)
    found = shutil.which("ffprobe")
    if found:
        return found
    return "ffprobe"
