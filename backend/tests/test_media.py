"""Direct tests for the FFmpeg media helpers (app/media.py).

These run real FFmpeg (present in the backend image), so run them in the
container: `docker compose exec api python -m pytest tests/test_media.py`.
They need no DB, Redis, MinIO, or worker.
"""
import pytest

from app import media
from app.pipeline import mock


@pytest.fixture(scope="module")
def keyframe() -> bytes:
    return mock.placeholder_png("media-test", width=32, height=18)


@pytest.fixture(scope="module")
def clip(keyframe) -> bytes:
    return media.image_to_clip(image_bytes=keyframe, duration=2.0, aspect_ratio="16:9")


def test_dims_for_each_aspect_ratio():
    assert media.dims_for("16:9") == (854, 480)
    assert media.dims_for("9:16") == (480, 854)
    assert media.dims_for("1:1") == (480, 480)
    assert media.dims_for("weird") == (854, 480)  # falls back to 16:9


def test_image_to_clip_makes_a_valid_mp4(clip):
    assert isinstance(clip, bytes) and len(clip) > 1000
    assert clip[4:8] == b"ftyp"  # ISO-BMFF / MP4 box signature


def test_clip_has_video_and_audio_streams_of_right_duration(clip):
    import subprocess, tempfile, json
    from pathlib import Path

    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "c.mp4"
        p.write_bytes(clip)
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries",
             "stream=codec_type,codec_name:format=duration",
             "-of", "json", str(p)],
            capture_output=True, text=True,
        ).stdout
    info = json.loads(out)
    codecs = {s["codec_type"]: s["codec_name"] for s in info["streams"]}
    assert codecs.get("video") == "h264"
    assert codecs.get("audio") == "aac"
    assert abs(float(info["format"]["duration"]) - 2.0) < 0.5


def test_demux_audio_returns_audio_bytes(clip):
    audio = media.demux_audio(video_bytes=clip)
    assert isinstance(audio, bytes) and len(audio) > 0


def test_extract_frames_count_and_format(clip):
    frames = media.extract_frames(video_bytes=clip, n=4)
    assert len(frames) == 4
    assert all(f[:2] == b"\xff\xd8" for f in frames)  # JPEG SOI marker


def test_extract_custom_frame_count(clip):
    assert len(media.extract_frames(video_bytes=clip, n=2)) == 2


def test_demux_audio_on_garbage_raises_ffmpeg_error():
    with pytest.raises(media.FFmpegError):
        media.demux_audio(video_bytes=b"not a video at all")


def test_synth_music_bed_and_duration():
    bed = media.synth_music_bed(bpm=120, seconds=3, style="ambient")
    assert isinstance(bed, bytes) and len(bed) > 500
    dur = media.duration_of(audio_or_video_bytes=bed, suffix=".mp3")
    assert 2.0 < dur < 4.5
