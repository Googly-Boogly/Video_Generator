"""FFmpeg helpers: encode, demux native audio, extract frames.

These run real FFmpeg (present in the backend image) even in mock mode — "mock"
means we don't pay an AI model, not that we skip local media work. That gives a
genuinely playable preview and exercises the assembly path used in Phase 5.
"""
from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

# Output resolution by aspect ratio (480p-class draft).
_DIMS = {
    "16:9": (854, 480),
    "9:16": (480, 854),
    "1:1": (480, 480),
}


class FFmpegError(RuntimeError):
    pass


def _run(args: list[str]) -> None:
    proc = subprocess.run(args, capture_output=True)
    if proc.returncode != 0:
        raise FFmpegError(proc.stderr.decode(errors="replace")[-1500:])


def dims_for(aspect_ratio: str) -> tuple[int, int]:
    return _DIMS.get(aspect_ratio, _DIMS["16:9"])


def image_to_clip(
    *, image_bytes: bytes, duration: float, aspect_ratio: str = "16:9", fps: int = 24,
) -> bytes:
    """Make a playable H.264/AAC clip from a still image (silent audio track).

    Used in mock mode to stand in for an image-to-video model. The image is
    scaled to fit and padded to the target frame, with a Ken-Burns-free static
    hold for `duration` seconds.
    """
    w, h = dims_for(aspect_ratio)
    with tempfile.TemporaryDirectory() as d:
        img = Path(d) / "in.png"
        out = Path(d) / "out.mp4"
        img.write_bytes(image_bytes)
        vf = (
            f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
            f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={fps}"
        )
        _run([
            "ffmpeg", "-y", "-loglevel", "error",
            "-loop", "1", "-i", str(img),
            "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
            "-t", f"{max(duration, 0.5):.2f}",
            "-vf", vf,
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "veryfast",
            "-c:a", "aac", "-shortest",
            "-movflags", "+faststart",
            str(out),
        ])
        return out.read_bytes()


def demux_audio(*, video_bytes: bytes) -> bytes:
    """Extract the clip's native audio track as an m4a (AAC).

    Every generated clip carries native ambience/Foley; we pull it into its own
    asset so it can be leveled independently (15–30% under narration).
    """
    with tempfile.TemporaryDirectory() as d:
        vid = Path(d) / "in.mp4"
        out = Path(d) / "audio.m4a"
        vid.write_bytes(video_bytes)
        _run([
            "ffmpeg", "-y", "-loglevel", "error",
            "-i", str(vid), "-vn", "-c:a", "aac", str(out),
        ])
        return out.read_bytes()


def extract_frames(*, video_bytes: bytes, n: int = 4) -> list[bytes]:
    """Grab `n` evenly spaced JPEG frames for the quality gate."""
    with tempfile.TemporaryDirectory() as d:
        vid = Path(d) / "in.mp4"
        vid.write_bytes(video_bytes)
        dur = _probe_duration(vid) or 1.0
        frames: list[bytes] = []
        for i in range(n):
            t = dur * (i + 0.5) / n
            out = Path(d) / f"f{i}.jpg"
            _run([
                "ffmpeg", "-y", "-loglevel", "error",
                "-ss", f"{t:.3f}", "-i", str(vid),
                "-frames:v", "1", "-q:v", "3", str(out),
            ])
            if out.exists():
                frames.append(out.read_bytes())
        return frames


def _probe_duration(path: Path) -> float | None:
    proc = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True,
    )
    try:
        return float(proc.stdout.decode().strip())
    except (ValueError, AttributeError):
        return None


def duration_of(*, audio_or_video_bytes: bytes, suffix: str = ".mp4") -> float:
    """Probe the duration (seconds) of an audio/video payload."""
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / f"m{suffix}"
        p.write_bytes(audio_or_video_bytes)
        return _probe_duration(p) or 0.0


def synth_music_bed(*, bpm: int, seconds: float, style: str = "ambient") -> bytes:
    """Synthesize a placeholder music bed with a clear beat at `bpm`.

    A low sustained tone plus a short click on every beat — crude, but it gives
    librosa real onsets to detect, so the beat-grid path is exercised without
    shipping copyrighted audio. Returns MP3 bytes.
    """
    beat = 60.0 / max(bpm, 1)
    base_freq = {"ambient": 110, "cinematic": 82, "upbeat": 147}.get(style, 110)
    with tempfile.TemporaryDirectory() as d:
        out = Path(d) / "bed.mp3"
        # Sustained pad + percussive click every `beat` seconds.
        pad = f"sine=frequency={base_freq}:duration={seconds:.2f}"
        click = (
            f"sine=frequency=1200:duration={seconds:.2f},"
            f"tremolo=f={1/beat:.4f}:d=1,"
            f"volume='if(lt(mod(t,{beat:.4f}),0.04),1.0,0.0)':eval=frame"
        )
        _run([
            "ffmpeg", "-y", "-loglevel", "error",
            "-f", "lavfi", "-i", pad,
            "-f", "lavfi", "-i", click,
            "-filter_complex", "[0:a]volume=0.25[a0];[1:a]volume=0.8[a1];[a0][a1]amix=inputs=2:normalize=0[a]",
            "-map", "[a]", "-c:a", "libmp3lame", "-b:a", "128k",
            str(out),
        ])
        return out.read_bytes()
