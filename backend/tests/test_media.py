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


def _probe_dims(data: bytes) -> tuple[int, int, set]:
    import subprocess, tempfile, json
    from pathlib import Path
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "v.mp4"
        p.write_bytes(data)
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries",
             "stream=codec_type,width,height", "-of", "json", str(p)],
            capture_output=True, text=True,
        ).stdout
    info = json.loads(out)
    v = next(s for s in info["streams"] if s["codec_type"] == "video")
    types = {s["codec_type"] for s in info["streams"]}
    return v["width"], v["height"], types


def _two_scene_render(draft: bool) -> bytes:
    clip_a = media.image_to_clip(image_bytes=mock.placeholder_png("a"), duration=1.5, aspect_ratio="16:9")
    clip_b = media.image_to_clip(image_bytes=mock.placeholder_png("b"), duration=1.5, aspect_ratio="16:9")
    scenes = [
        {"clip_bytes": clip_a, "narration_bytes": mock.silent_wav(1.0), "trim_head": 0.1,
         "trim_tail": 0.1, "caption": "Scene one, a test", "audio_mode": "narrated",
         "narration_db": 0.0, "native_db": -16.0, "duration": 1.5},
        {"clip_bytes": clip_b, "narration_bytes": None, "trim_head": 0.1, "trim_tail": 0.1,
         "caption": "Scene two", "audio_mode": "dialogue", "narration_db": None,
         "native_db": 0.0, "duration": 1.5},
    ]
    music = media.synth_music_bed(bpm=120, seconds=6, style="ambient")
    return media.assemble_video(draft=draft, aspect_ratio="16:9", scenes=scenes, music_bytes=music)


def test_assemble_draft_is_480p_with_audio():
    out = _two_scene_render(draft=True)
    assert out[4:8] == b"ftyp"
    w, h, types = _probe_dims(out)
    assert (w, h) == (854, 480)
    assert "video" in types and "audio" in types


def test_assemble_final_is_1080p():
    out = _two_scene_render(draft=False)
    w, h, _ = _probe_dims(out)
    assert (w, h) == (1920, 1080)


def test_assemble_is_narration_led_with_synced_captions():
    # `screen_time` drives each scene's on-screen length: clone-pad a clip shorter
    # than its narration, trim one that's longer. Multiple time-coded caption events
    # per scene (sentence-level sync) must not break the filtergraph, and the total
    # duration must equal the summed narration-led screen time.
    clip_short = media.image_to_clip(image_bytes=mock.placeholder_png("s"), duration=1.0, aspect_ratio="16:9")
    clip_long = media.image_to_clip(image_bytes=mock.placeholder_png("l"), duration=4.0, aspect_ratio="16:9")
    scenes = [
        {"clip_bytes": clip_short, "narration_bytes": mock.silent_wav(3.0), "screen_time": 3.0,
         "trim_head": 0.0, "audio_mode": "narrated", "narration_db": 0.0, "native_db": -16.0,
         "captions": [{"text": "First half.", "start": 0.0, "end": 1.5},
                      {"text": "Second half.", "start": 1.5, "end": 3.0}]},
        {"clip_bytes": clip_long, "narration_bytes": mock.silent_wav(2.0), "screen_time": 2.0,
         "trim_head": 0.0, "audio_mode": "narrated", "narration_db": 0.0, "native_db": -16.0,
         "captions": [{"text": "Just one line.", "start": 0.0, "end": 2.0}]},
    ]
    out = media.assemble_video(
        draft=True, aspect_ratio="16:9", scenes=scenes,
        music_bytes=media.synth_music_bed(bpm=120, seconds=6, style="ambient"),
    )
    assert out[4:8] == b"ftyp"
    dur = media.duration_of(audio_or_video_bytes=out, suffix=".mp4")
    assert abs(dur - 5.0) < 0.5  # 3.0 (clone-padded) + 2.0 (trimmed) screen time
