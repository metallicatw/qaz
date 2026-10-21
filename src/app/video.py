from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


def ffmpeg_path() -> str | None:
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


def make_video(image: Path, out: Path, size: tuple[int, int], seconds: int = 8, fps: int = 30) -> Path:
    exe = ffmpeg_path()
    if not exe:
        raise RuntimeError("ffmpeg not found (install ffmpeg or pip install imageio-ffmpeg)")
    w, h = size
    frames = seconds * fps
    vf = (f"scale={w * 2}:{h * 2}:force_original_aspect_ratio=decrease,pad={w * 2}:{h * 2}:(ow-iw)/2:(oh-ih)/2:white,"
          f"zoompan=z='min(1+0.10*on/{frames},1.10)':d={frames}:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s={w}x{h}:fps={fps},"
          f"format=yuv420p")
    out.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([exe, "-y", "-loglevel", "error", "-loop", "1", "-i", str(image), "-vf", vf, "-frames:v", str(frames),
                    "-c:v", "libx264", "-preset", "veryfast", "-crf", "26", "-movflags", "+faststart", "-an", str(out)],
                   check=True, timeout=300)
    return out
