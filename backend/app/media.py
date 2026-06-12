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


FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"

# Output resolution per render tier.
_DRAFT_DIMS = {"16:9": (854, 480), "9:16": (480, 854), "1:1": (480, 480)}
_FINAL_DIMS = {"16:9": (1920, 1080), "9:16": (1080, 1920), "1:1": (1080, 1080)}


def assemble_video(
    *, draft: bool, aspect_ratio: str, scenes: list[dict],
    music_bytes: bytes | None = None, music_db: float = -18.0, fps: int = 24,
) -> bytes:
    """Execute an EDL with FFmpeg into a single H.264/AAC film.

    Each scene dict: {clip_bytes, narration_bytes?, trim_head, trim_tail, caption,
    audio_mode, narration_db, native_db, duration}. Builds the hybrid audio mix:
    native (ducked) from each clip + narration (delayed per scene) + a music bed,
    burns captions, and watermarks the draft. Returns mp4 bytes.
    """
    dims = (_DRAFT_DIMS if draft else _FINAL_DIMS).get(aspect_ratio, (854, 480) if draft else (1920, 1080))
    w, h = dims
    af = "aformat=sample_rates=44100:channel_layouts=stereo"

    with tempfile.TemporaryDirectory() as d:
        dp = Path(d)
        in_args: list[str] = []
        count = 0

        def add_input(path: Path) -> int:
            nonlocal count
            in_args.extend(["-i", str(path)])
            idx = count
            count += 1
            return idx

        # Clip inputs (each provides [i:v] and [i:a] = native audio).
        clip_idx, durs = [], []
        for i, s in enumerate(scenes):
            f = dp / f"clip{i}.mp4"
            f.write_bytes(s["clip_bytes"])
            clip_idx.append(add_input(f))
            durs.append(_probe_duration(f) or float(s.get("duration", 5.0)))

        # Narration inputs (narrated scenes only).
        narr_idx: dict[int, int] = {}
        for i, s in enumerate(scenes):
            nb = s.get("narration_bytes")
            if nb and s.get("narration_db") is not None and s.get("audio_mode") != "dialogue":
                f = dp / f"narr{i}.wav"
                f.write_bytes(nb)
                narr_idx[i] = add_input(f)

        music_index = None
        if music_bytes:
            f = dp / "music.aud"
            f.write_bytes(music_bytes)
            music_index = add_input(f)

        # Trimmed durations + timeline offsets.
        trims, offsets, t = [], [], 0.0
        for i, s in enumerate(scenes):
            th = max(0.0, float(s.get("trim_head", 0) or 0))
            tt = max(0.0, float(s.get("trim_tail", 0) or 0))
            tdur = max(0.3, durs[i] - th - tt)
            trims.append((th, tdur))
            offsets.append(t)
            t += tdur
        total = t

        fc: list[str] = []

        # --- Video: trim/scale/caption + fade transitions, then concat ---
        # A non-"cut" transition (or the first/last clip) gets a dip-to-black
        # fade — reliable and timeline-exact, so the audio mix stays in sync.
        n = len(scenes)
        vlabels = []
        for i, s in enumerate(scenes):
            th, tdur = trims[i]
            chain = (
                f"[{clip_idx[i]}:v]trim=start={th:.3f}:end={th + tdur:.3f},setpts=PTS-STARTPTS,"
                f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
                f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={fps}"
            )
            fd = min(0.4, tdur / 3)
            fade_in = i == 0 or s.get("transition", "cut") != "cut"
            fade_out = i == n - 1 or (i + 1 < n and scenes[i + 1].get("transition", "cut") != "cut")
            if fade_in:
                chain += f",fade=t=in:st=0:d={fd:.3f}"
            if fade_out:
                chain += f",fade=t=out:st={tdur - fd:.3f}:d={fd:.3f}"
            cap = (s.get("caption") or "").strip()
            if cap:
                cf = dp / f"cap{i}.txt"
                cf.write_text(cap)
                fs = max(18, int(h * 0.05))
                chain += (
                    f",drawtext=fontfile={FONT}:textfile={cf}:x=(w-text_w)/2:y=h-{fs * 2}:"
                    f"fontsize={fs}:fontcolor=white:box=1:boxcolor=black@0.5:boxborderw=10"
                )
            fc.append(f"{chain}[v{i}]")
            vlabels.append(f"[v{i}]")
        fc.append("".join(vlabels) + f"concat=n={len(scenes)}:v=1:a=0[vcat]")
        if draft:
            fs = max(16, int(h * 0.05))
            fc.append(
                f"[vcat]drawtext=fontfile={FONT}:text='DRAFT':x=20:y=20:fontsize={fs}:"
                f"fontcolor=white@0.45:box=1:boxcolor=black@0.25:boxborderw=6[vout]"
            )
        else:
            fc.append("[vcat]null[vout]")

        # --- Native audio: trim+level each clip's track, concat ---
        nlabels = []
        for i, s in enumerate(scenes):
            th, tdur = trims[i]
            db = s.get("native_db")
            vol = -100.0 if db is None else float(db)  # muted/None -> silence
            fc.append(
                f"[{clip_idx[i]}:a]atrim=start={th:.3f}:end={th + tdur:.3f},asetpts=PTS-STARTPTS,"
                f"volume={vol}dB,{af}[na{i}]"
            )
            nlabels.append(f"[na{i}]")
        fc.append("".join(nlabels) + f"concat=n={len(scenes)}:v=0:a=1[natcat]")

        # --- Narration: delay each to its scene offset, mix ---
        narr_labels = []
        for i, s in enumerate(scenes):
            if i in narr_idx:
                off = int(offsets[i] * 1000)
                ndb = float(s.get("narration_db", 0.0) or 0.0)
                fc.append(f"[{narr_idx[i]}:a]adelay={off}|{off},volume={ndb}dB,{af}[nr{i}]")
                narr_labels.append(f"[nr{i}]")
        have_narr = bool(narr_labels)
        if have_narr:
            fc.append("".join(narr_labels) + f"amix=inputs={len(narr_labels)}:normalize=0[narrmix]")

        # --- Music bed: pad/trim to total, level ---
        have_music = music_index is not None
        if have_music:
            fc.append(
                f"[{music_index}:a]{af},apad,atrim=0:{total:.3f},asetpts=PTS-STARTPTS,"
                f"volume={music_db}dB[musicraw]"
            )

        # --- Final mix: native + narration + (ducked) music + limiter ---
        mix_inputs = ["[natcat]"]
        if have_narr and have_music:
            # Sidechain-duck the music under the narration.
            fc.append("[narrmix]asplit=2[narrout][narrkey]")
            fc.append("[musicraw][narrkey]sidechaincompress=threshold=0.03:ratio=8:"
                      "attack=20:release=400[music]")
            mix_inputs += ["[narrout]", "[music]"]
        elif have_narr:
            mix_inputs.append("[narrmix]")
        elif have_music:
            mix_inputs.append("[musicraw]")

        if len(mix_inputs) == 1:
            fc.append(f"{mix_inputs[0]}alimiter=limit=0.95[aout]")
        else:
            fc.append(
                "".join(mix_inputs) + f"amix=inputs={len(mix_inputs)}:normalize=0[mx];"
                "[mx]alimiter=limit=0.95[aout]"
            )

        out = dp / "out.mp4"
        _run([
            "ffmpeg", "-y", "-loglevel", "error", *in_args,
            "-filter_complex", ";".join(fc),
            "-map", "[vout]", "-map", "[aout]",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "veryfast",
            "-crf", "28" if draft else "20",
            "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart",
            "-t", f"{total:.3f}",
            str(out),
        ])
        return out.read_bytes()


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
