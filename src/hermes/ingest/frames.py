"""Extract evenly-spaced keyframes from a video file via ffmpeg."""
from __future__ import annotations
import subprocess
from pathlib import Path
from loguru import logger


def extract_keyframes(
    video_path: Path,
    out_dir: Path,
    n: int = 4,
    timeout: int = 120,
) -> list[Path]:
    """Sample n evenly-spaced frames from the video.

    Probes duration with ffprobe, then issues n single-frame ffmpeg seeks
    spaced across the duration (skipping the first/last 5%). Saves as
    <out_dir>/frame_NN.jpg. Returns produced paths in time order. Empty
    list if no frame can be produced (e.g. ffmpeg fails on every shot).

    If ffprobe can't determine the duration (live streams, weird containers),
    falls back to grabbing a single frame at t=1s.
    """
    video_path = Path(video_path)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    if not video_path.exists():
        raise FileNotFoundError(f"video not found: {video_path}")

    # Probe duration first so we can compute the keyframe interval.
    try:
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(video_path)],
            capture_output=True, text=True, timeout=30,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        raise RuntimeError(f"ffprobe unavailable or timed out: {e}") from e
    if probe.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {probe.stderr.strip()[:200]}")
    try:
        duration = float(probe.stdout.strip())
    except ValueError:
        duration = 0.0

    if duration <= 0:
        # Fallback: just grab one frame at 1s.
        ts_list = ["1"]
    else:
        # Pick n timestamps spread across the duration, skipping first/last 5%.
        usable = max(0.5, duration * 0.9)
        start = duration * 0.05
        ts_list = [f"{start + (i + 0.5) * usable / n:.2f}" for i in range(n)]

    out_paths: list[Path] = []
    for i, ts in enumerate(ts_list):
        out = out_dir / f"frame_{i:02d}.jpg"
        cmd = [
            "ffmpeg", "-y", "-loglevel", "error",
            "-ss", ts, "-i", str(video_path),
            "-frames:v", "1", "-q:v", "3",
            str(out),
        ]
        logger.debug("ffmpeg keyframe: {}", " ".join(cmd))
        try:
            result = subprocess.run(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                timeout=timeout,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired) as e:
            logger.warning("ffmpeg keyframe shot failed at ts={}: {}", ts, e)
            continue
        if result.returncode == 0 and out.exists():
            out_paths.append(out)
        else:
            err = (result.stderr or b"").decode("utf-8", errors="replace").strip()
            logger.debug("ffmpeg keyframe rc={} err={}", result.returncode, err[:200])
    return out_paths
