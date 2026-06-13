"""End-to-end API tests against the in-process app (eager Celery, SQLite, mock).

Mirrors the live smoke sweep (scripts/smoke_test.py) but runs with zero infra.
"""


def _make_project(client, **over):
    body = {"idea": "A clockmaker who repairs memories", "target_length": 15,
            "aspect_ratio": "9:16", "style_preset": "noir"}
    body.update(over)
    r = client.post("/api/projects", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def _storyboard(client, pid):
    r = client.post(f"/api/projects/{pid}/storyboard")
    assert r.status_code == 202, r.text
    job = r.json()
    # Eager mode means the task already ran; the job is terminal.
    final = client.get(f"/api/jobs/{job['id']}").json()
    assert final["status"] == "success", final
    return final


# --- config / health --------------------------------------------------------

def test_health_and_config(client):
    assert client.get("/health").json()["mock_generation"] is True
    cfg = client.get("/api/config").json()
    assert len(cfg["models"]) == 7
    assert "kling-3-pro" in cfg["video_models"]
    assert len(cfg["style_presets"]) >= 5


# --- validation -------------------------------------------------------------

def test_validation_errors(client):
    assert client.post("/api/projects", json={"idea": "x"}).status_code == 422
    assert client.post("/api/projects", json={"idea": "long enough", "target_length": 45}).status_code == 422
    assert client.get("/api/projects/nope").status_code == 404
    assert client.get("/api/jobs/nope").status_code == 404


# --- lifecycle --------------------------------------------------------------

def test_storyboard_lifecycle(client):
    p = _make_project(client)
    assert p["status"] == "draft"
    _storyboard(client, p["id"])
    p = client.get(f"/api/projects/{p['id']}").json()
    assert p["status"] == "storyboarded"
    assert p["style_bible"] is not None
    scenes = p["scenes"]
    assert len(scenes) >= 3
    assert abs(sum(s["duration_seconds"] for s in scenes) - 15) <= 6
    assert [s["scene_number"] for s in scenes] == list(range(1, len(scenes) + 1))


def test_scene_editing_and_routing(client):
    p = _make_project(client)
    _storyboard(client, p["id"])
    pid = p["id"]
    s = client.get(f"/api/projects/{pid}/scenes").json()[0]
    sid = s["id"]

    r = client.patch(f"/api/projects/{pid}/scenes/{sid}",
                     json={"shot_description": "EDITED", "duration_seconds": 4})
    assert r.json()["shot_description"] == "EDITED" and r.json()["duration_seconds"] == 4.0

    # Dialogue auto-routes to a lip-sync model.
    r = client.patch(f"/api/projects/{pid}/scenes/{sid}",
                     json={"audio_mode": "dialogue", "dialogue_text": "tick"})
    assert r.json()["suggested_model"] == "veo-31"

    # Bad override rejected; good one accepted.
    assert client.patch(f"/api/projects/{pid}/scenes/{sid}", json={"model_override": "bogus"}).status_code == 400
    assert client.patch(f"/api/projects/{pid}/scenes/{sid}", json={"model_override": "seedance-2"}).json()["model_override"] == "seedance-2"


def test_reorder_add_delete(client):
    p = _make_project(client)
    _storyboard(client, p["id"])
    pid = p["id"]
    scenes = client.get(f"/api/projects/{pid}/scenes").json()
    ids = [s["id"] for s in scenes]

    ro = client.post(f"/api/projects/{pid}/scenes/reorder", json={"scene_ids": list(reversed(ids))})
    assert ro.status_code == 200
    assert [s["scene_number"] for s in ro.json()] == list(range(1, len(ids) + 1))

    assert client.post(f"/api/projects/{pid}/scenes/reorder", json={"scene_ids": ids[:-1]}).status_code == 400

    n0 = len(ids)
    assert client.post(f"/api/projects/{pid}/scenes", json={"after_scene_number": 1}).status_code == 201
    lst = client.get(f"/api/projects/{pid}/scenes").json()
    assert len(lst) == n0 + 1
    assert [s["scene_number"] for s in lst] == list(range(1, len(lst) + 1))

    assert client.delete(f"/api/projects/{pid}/scenes/{lst[-1]['id']}").status_code == 204
    lst = client.get(f"/api/projects/{pid}/scenes").json()
    assert len(lst) == n0


def test_conversational_revision(client):
    p = _make_project(client)
    _storyboard(client, p["id"])
    pid = p["id"]
    r = client.post(f"/api/projects/{pid}/scenes/revise", json={"instruction": "make scene 2 moodier"})
    assert r.status_code == 202
    assert client.get(f"/api/jobs/{r.json()['id']}").json()["status"] == "success"
    scenes = client.get(f"/api/projects/{pid}/scenes").json()
    assert "revised" in scenes[1]["shot_description"]


def test_cost_premium_vs_draft(client):
    p = _make_project(client, target_length=30, aspect_ratio="16:9")
    _storyboard(client, p["id"])
    pid = p["id"]
    premium = client.get(f"/api/projects/{pid}/cost?tier=premium").json()
    draft = client.get(f"/api/projects/{pid}/cost?tier=draft").json()
    assert premium["total"] > draft["total"]
    assert len(premium["line_items"]) == 3


def test_delete_project(client):
    p = _make_project(client)
    pid = p["id"]
    assert client.delete(f"/api/projects/{pid}").status_code == 204
    assert client.get(f"/api/projects/{pid}").status_code == 404


# --- Phase 2: keyframes -----------------------------------------------------

def _keyframes(client, pid):
    r = client.post(f"/api/projects/{pid}/keyframes")
    assert r.status_code == 202, r.text
    final = client.get(f"/api/jobs/{r.json()['id']}").json()
    assert final["status"] == "success", final
    return final


def test_keyframes_best_of_n_flow(client):
    p = _make_project(client, target_length=15)
    _storyboard(client, p["id"])
    pid = p["id"]
    job = _keyframes(client, pid)
    assert job["result"]["scenes_done"] >= 1

    # Reference images were generated once.
    refs = client.get(f"/api/projects/{pid}/references").json()
    assert len(refs) >= 3
    assert refs[0]["meta"]["role"] == "character"

    p = client.get(f"/api/projects/{pid}").json()
    assert p["status"] == "keyframes"
    for s in p["scenes"]:
        assert s["status"] == "done"
        assert s["keyframe_asset_id"]
        kfs = client.get(f"/api/projects/{pid}/scenes/{s['id']}/keyframes").json()
        assert len(kfs) == 3
        winners = [k for k in kfs if k["meta"]["is_winner"]]
        assert len(winners) == 1
        assert winners[0]["id"] == s["keyframe_asset_id"]


def test_keyframe_content_served(client):
    p = _make_project(client, target_length=15)
    _storyboard(client, p["id"])
    pid = p["id"]
    _keyframes(client, pid)
    sid = client.get(f"/api/projects/{pid}/scenes").json()[0]["id"]
    kf = client.get(f"/api/projects/{pid}/scenes/{sid}/keyframes").json()[0]
    r = client.get(kf["url"])
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("image/")
    assert r.content[:4] == b"\x89PNG"


def test_select_keyframe_override(client):
    p = _make_project(client, target_length=15)
    _storyboard(client, p["id"])
    pid = p["id"]
    _keyframes(client, pid)
    sid = client.get(f"/api/projects/{pid}/scenes").json()[0]["id"]
    kfs = client.get(f"/api/projects/{pid}/scenes/{sid}/keyframes").json()
    runner_up = next(k for k in kfs if not k["meta"]["is_winner"])

    s = client.post(f"/api/projects/{pid}/scenes/{sid}/keyframe/select",
                    json={"asset_id": runner_up["id"]}).json()
    assert s["keyframe_asset_id"] == runner_up["id"]
    kfs2 = client.get(f"/api/projects/{pid}/scenes/{sid}/keyframes").json()
    winners = [k for k in kfs2 if k["meta"]["is_winner"]]
    assert len(winners) == 1 and winners[0]["id"] == runner_up["id"]


def test_regenerate_single_scene_keyframes(client):
    p = _make_project(client, target_length=15)
    _storyboard(client, p["id"])
    pid = p["id"]
    _keyframes(client, pid)
    sid = client.get(f"/api/projects/{pid}/scenes").json()[0]["id"]
    before = {k["id"] for k in client.get(f"/api/projects/{pid}/scenes/{sid}/keyframes").json()}
    r = client.post(f"/api/projects/{pid}/scenes/{sid}/keyframes")
    assert r.status_code == 202
    assert client.get(f"/api/jobs/{r.json()['id']}").json()["status"] == "success"
    after = {k["id"] for k in client.get(f"/api/projects/{pid}/scenes/{sid}/keyframes").json()}
    assert len(after) == 3 and before.isdisjoint(after)  # fresh assets


def test_keyframes_requires_scenes(client):
    p = _make_project(client)
    assert client.post(f"/api/projects/{p['id']}/keyframes").status_code == 400


# --- Phase 3: video + quality gate ------------------------------------------

def _video(client, pid, tier="draft"):
    r = client.post(f"/api/projects/{pid}/video?tier={tier}")
    assert r.status_code == 202, r.text
    final = client.get(f"/api/jobs/{r.json()['id']}").json()
    assert final["status"] == "success", final
    return final


def _ready_project(client, **over):
    p = _make_project(client, target_length=15, **over)
    _storyboard(client, p["id"])
    _keyframes(client, p["id"])
    return p


def test_video_flow_clips_audio_frames_quality(client):
    p = _ready_project(client)
    pid = p["id"]
    job = _video(client, pid)
    assert job["result"]["scenes_done"] >= 1

    p = client.get(f"/api/projects/{pid}").json()
    assert p["status"] == "clips"
    for s in p["scenes"]:
        assert s["status"] in ("done", "flagged")
        assert s["clip_asset_id"] and s["native_audio_asset_id"]
        assert s["quality"] is not None and "flagged" in s["quality"]
        # clip is a real, playable mp4
        clip = client.get(f"/api/assets/{s['clip_asset_id']}").json()
        assert clip["content_type"] == "video/mp4"
        body = client.get(clip["url"]).content
        assert body[4:8] == b"ftyp"
        # 4 quality-gate frames
        frames = client.get(f"/api/projects/{pid}/scenes/{s['id']}/frames").json()
        assert len(frames) == 4


def test_video_requires_keyframes(client):
    p = _make_project(client, target_length=15)
    _storyboard(client, p["id"])
    # No keyframes yet -> 400
    assert client.post(f"/api/projects/{p['id']}/video").status_code == 400


def test_single_scene_video_regenerate(client):
    p = _ready_project(client)
    pid = p["id"]
    _video(client, pid)
    sid = client.get(f"/api/projects/{pid}/scenes").json()[0]["id"]
    before = client.get(f"/api/projects/{pid}/scenes").json()[0]["clip_asset_id"]
    r = client.post(f"/api/projects/{pid}/scenes/{sid}/video")
    assert r.status_code == 202
    assert client.get(f"/api/jobs/{r.json()['id']}").json()["status"] == "success"
    after = client.get(f"/api/projects/{pid}/scenes/{sid}").json()
    assert after["clip_asset_id"] and after["clip_asset_id"] != before


def test_failed_scene_is_isolated(client):
    """A scene with no winning keyframe fails alone — the project survives and
    every other scene still gets a clip."""
    p = _ready_project(client)
    pid = p["id"]
    # Add a brand-new scene AFTER keyframes; it has no keyframe winner.
    bad = client.post(f"/api/projects/{pid}/scenes", json={"after_scene_number": 1}).json()

    job = _video(client, pid)
    assert job["result"]["scenes_failed"] >= 1
    assert job["result"]["scenes_done"] >= 1

    bad_scene = client.get(f"/api/projects/{pid}/scenes/{bad['id']}").json()
    assert bad_scene["status"] == "failed"
    assert bad_scene["error"] and "keyframe" in bad_scene["error"].lower()
    assert bad_scene["clip_asset_id"] is None

    # The project still advanced and the other scenes have clips.
    proj = client.get(f"/api/projects/{pid}").json()
    assert proj["status"] == "clips"
    good = [s for s in proj["scenes"] if s["id"] != bad["id"]]
    assert good and all(s["clip_asset_id"] for s in good)


def test_native_audio_asset_defaults_unmuted(client):
    p = _ready_project(client)
    pid = p["id"]
    _video(client, pid)
    s = client.get(f"/api/projects/{pid}/scenes").json()[0]
    native = client.get(f"/api/assets/{s['native_audio_asset_id']}").json()
    assert native["kind"] == "native_audio"
    assert native["content_type"] == "audio/mp4"
    assert native["meta"]["muted"] is False


def test_premium_tier_routes_premium_model(client):
    p = _ready_project(client)
    pid = p["id"]
    _video(client, pid, tier="premium")
    sid = client.get(f"/api/projects/{pid}/scenes").json()[0]["id"]
    clip_id = client.get(f"/api/projects/{pid}/scenes/{sid}").json()["clip_asset_id"]
    clip = client.get(f"/api/assets/{clip_id}").json()
    assert clip["meta"]["model_id"] == "kling-3-pro"  # premium narrated default


def test_full_regenerate_keyframes_no_cascade(client):
    """Regression: a full re-run with pre-existing assets must not trip the
    delete-orphan cascade (the bug fixed alongside Phase 4)."""
    p = _make_project(client, target_length=15)
    _storyboard(client, p["id"])
    pid = p["id"]
    _keyframes(client, pid)
    job = _keyframes(client, pid)  # second full run over existing keyframes
    assert job["result"]["scenes_done"] >= 1
    # variants not duplicated
    sid = client.get(f"/api/projects/{pid}/scenes").json()[0]["id"]
    assert len(client.get(f"/api/projects/{pid}/scenes/{sid}/keyframes").json()) == 3


# --- Phase 4: audio build ---------------------------------------------------

def _audio(client, pid):
    r = client.post(f"/api/projects/{pid}/audio")
    assert r.status_code == 202, r.text
    final = client.get(f"/api/jobs/{r.json()['id']}").json()
    assert final["status"] == "success", final
    return final


def test_audio_catalogs(client):
    v = client.get("/api/voices").json()
    assert any(x["voice_id"] == v["default"] for x in v["voices"])
    lib = client.get("/api/music/library").json()
    assert len(lib["tracks"]) >= 3


def test_set_voice(client):
    p = _make_project(client)
    pid = p["id"]
    assert client.post(f"/api/projects/{pid}/voice", json={"voice_id": "bogus"}).status_code == 400
    r = client.post(f"/api/projects/{pid}/voice", json={"voice_id": "voice_atlas"})
    assert r.json()["voice_id"] == "voice_atlas"


def test_music_library_pick_runs_librosa_beat_grid(client):
    p = _make_project(client, target_length=15)
    _storyboard(client, p["id"])
    pid = p["id"]
    r = client.post(f"/api/projects/{pid}/music/library", json={"track_id": "upbeat-128"})
    assert r.status_code == 201, r.text
    grid = r.json()["meta"]["beat_grid"]
    assert grid["engine"] == "librosa" and grid["bpm"] > 0 and len(grid["beats"]) > 5
    assert client.get(f"/api/projects/{pid}/music").json()["id"] == r.json()["id"]


def test_audio_build_narration_and_rebuild(client):
    p = _make_project(client, target_length=15)
    _storyboard(client, p["id"])
    pid = p["id"]
    job = _audio(client, pid)
    assert job["result"]["narrated"] >= 1

    narr = client.get(f"/api/projects/{pid}/narration").json()
    assert len(narr) == job["result"]["narrated"]
    assert narr[0]["meta"]["voice_id"] and narr[0]["meta"]["duration"] > 0
    assert client.get(narr[0]["url"]).content[:4] == b"RIFF"  # playable WAV
    assert client.get(f"/api/projects/{pid}").json()["status"] == "audio"

    # REBUILD — the cascade-bug regression. Must succeed and not duplicate.
    job2 = _audio(client, pid)
    assert job2["result"]["narrated"] >= 1
    assert len(client.get(f"/api/projects/{pid}/narration").json()) == len(narr)


def test_dialogue_scene_skips_narration(client):
    p = _make_project(client, target_length=15)
    _storyboard(client, p["id"])
    pid = p["id"]
    sid = client.get(f"/api/projects/{pid}/scenes").json()[0]["id"]
    client.patch(f"/api/projects/{pid}/scenes/{sid}",
                 json={"audio_mode": "dialogue", "dialogue_text": "We found it."})
    job = _audio(client, pid)
    assert job["result"]["skipped"] >= 1
    narr = client.get(f"/api/projects/{pid}/narration").json()
    assert all(a["scene_id"] != sid for a in narr)  # no narration for the dialogue scene

    mp = client.get(f"/api/projects/{pid}/mix-plan").json()
    s = next(x for x in mp["scenes"] if x["scene_number"] == 1)
    assert s["mix"]["pause_narration_for_dialogue"] is True
    assert s["mix"]["narration_db"] is None


# --- Multi-LLM selection ------------------------------------------------------

def test_llm_catalog_and_per_project_selection(client):
    cfg = client.get("/api/config").json()
    ids = {l["id"] for l in cfg["llms"]}
    assert {"gpt-5.4-nano", "claude-haiku-4-6"} <= ids
    assert cfg["default_llm"] in ids
    providers = {l["id"]: l["provider"] for l in cfg["llms"]}
    assert providers["gpt-5.4-nano"] == "openai"
    assert providers["claude-haiku-4-6"] == "anthropic"

    # Default when unspecified.
    p = client.post("/api/projects", json={"idea": "default llm project"}).json()
    assert p["llm_model"] == cfg["default_llm"]
    # Explicit pick is persisted.
    p2 = client.post("/api/projects",
                     json={"idea": "haiku project", "llm_model": "claude-haiku-4-6"}).json()
    assert p2["llm_model"] == "claude-haiku-4-6"
    assert client.get(f"/api/projects/{p2['id']}").json()["llm_model"] == "claude-haiku-4-6"
    # Unknown id rejected.
    assert client.post("/api/projects",
                       json={"idea": "bad llm", "llm_model": "gpt-9-ultra"}).status_code == 400


# --- Robustness ---------------------------------------------------------------

def test_delete_project_cleans_storage(client, storage_mem):
    p = _make_project(client, target_length=15)
    _storyboard(client, p["id"])
    pid = p["id"]
    _keyframes(client, pid)
    assert [k for k in storage_mem if pid in k]  # blobs exist
    assert client.delete(f"/api/projects/{pid}").status_code == 204
    assert [k for k in storage_mem if pid in k] == []  # MinIO objects cleaned up


def test_regenerate_cleans_old_blobs(client, storage_mem):
    p = _make_project(client, target_length=15)
    _storyboard(client, p["id"])
    pid = p["id"]
    _keyframes(client, pid)
    prefix = f"projects/{pid}/keyframe/"
    before = {k for k in storage_mem if prefix in k}
    _keyframes(client, pid)  # full re-run
    after = {k for k in storage_mem if prefix in k}
    assert len(after) == len(before)  # no accumulation
    assert before.isdisjoint(after)   # old blobs deleted, new ones written


def test_all_dialogue_audio_advances_status(client):
    p = _edit_ready(client)  # storyboard + keyframes + video → has clips
    pid = p["id"]
    for s in client.get(f"/api/projects/{pid}/scenes").json():
        client.patch(f"/api/projects/{pid}/scenes/{s['id']}",
                     json={"audio_mode": "dialogue", "dialogue_text": "x"})
    job = _audio(client, pid)
    assert job["result"]["narrated"] == 0 and job["result"]["skipped"] >= 1
    assert client.get(f"/api/projects/{pid}").json()["status"] == "audio"  # still advances


def test_concurrent_job_guard(client):
    p = _make_project(client, target_length=15)
    _storyboard(client, p["id"])
    pid = p["id"]
    from app.database import SessionLocal
    from app.models import Job
    db = SessionLocal()
    db.add(Job(project_id=pid, type="keyframes", status="running"))
    db.commit()
    db.close()
    assert client.post(f"/api/projects/{pid}/keyframes").status_code == 409


def test_single_scene_render(client):
    p = _make_project(client, target_length=15)
    _storyboard(client, p["id"])
    pid = p["id"]
    scenes = client.get(f"/api/projects/{pid}/scenes").json()
    for s in scenes[1:]:
        client.delete(f"/api/projects/{pid}/scenes/{s['id']}")
    assert len(client.get(f"/api/projects/{pid}/scenes").json()) == 1
    _keyframes(client, pid)
    _video(client, pid)
    _run(client, f"/api/projects/{pid}/edl")
    dr = _run(client, f"/api/projects/{pid}/render?final=false")
    assert dr["result"]["kind"] == "draft"  # single-clip concat renders fine


# --- Phase 6: cost dashboard --------------------------------------------------

def test_cost_dashboard_records_actual_spend(client):
    p = _make_project(client, target_length=15)
    _storyboard(client, p["id"])
    pid = p["id"]
    _keyframes(client, pid)
    _video(client, pid)
    _audio(client, pid)

    d = client.get(f"/api/projects/{pid}/costs").json()
    assert d["mock"] is True
    assert d["estimated"]["total"] > 0
    assert d["actual"]["total"] > 0
    # every paid step recorded something
    assert d["actual"]["by_step"]["keyframes"] > 0
    assert d["actual"]["by_step"]["video"] > 0
    assert d["actual"]["by_step"]["audio"] > 0
    assert len(d["actual"]["entries"]) >= 3
    assert d["actual"]["entries"][0]["mock"] is True
    # by_step sums to the actual total
    assert abs(sum(d["actual"]["by_step"].values()) - d["actual"]["total"]) < 1e-6


def test_cost_ledger_accumulates_on_rerun(client):
    p = _make_project(client, target_length=15)
    _storyboard(client, p["id"])
    pid = p["id"]
    _keyframes(client, pid)
    before = client.get(f"/api/projects/{pid}/costs").json()["actual"]["total"]
    sid = client.get(f"/api/projects/{pid}/scenes").json()[0]["id"]
    r = client.post(f"/api/projects/{pid}/scenes/{sid}/keyframes")
    assert client.get(f"/api/jobs/{r.json()['id']}").json()["status"] == "success"
    after = client.get(f"/api/projects/{pid}/costs").json()["actual"]["total"]
    assert after > before  # re-running adds to the ledger (regeneration waste)


def test_final_render_adds_premium_render_cost(client):
    p = _edit_ready(client)
    pid = p["id"]
    sid = client.get(f"/api/projects/{pid}/scenes").json()[0]["id"]
    client.patch(f"/api/projects/{pid}/scenes/{sid}",
                 json={"audio_mode": "dialogue", "dialogue_text": "hi"})
    _run(client, f"/api/projects/{pid}/edl")
    _run(client, f"/api/projects/{pid}/render?final=true")
    by_step = client.get(f"/api/projects/{pid}/costs").json()["actual"]["by_step"]
    assert by_step.get("render", 0) > 0  # hero scene regenerated at premium


# --- Phase 5: AI editor (EDL) + render ----------------------------------------

def _edit_ready(client, **over):
    p = _make_project(client, target_length=15, **over)
    _storyboard(client, p["id"])
    _keyframes(client, p["id"])
    _video(client, p["id"])
    return p


def _run(client, path):
    r = client.post(path)
    assert r.status_code == 202, r.text
    final = client.get(f"/api/jobs/{r.json()['id']}").json()
    assert final["status"] == "success", final
    return final


def test_edl_then_draft_then_final_render(client):
    p = _edit_ready(client)
    pid = p["id"]

    job = _run(client, f"/api/projects/{pid}/edl")
    assert job["result"]["cuts"] >= 1
    edl = client.get(f"/api/projects/{pid}/edl").json()
    assert edl["total_duration"] > 0 and len(edl["cuts"]) >= 1
    assert client.get(f"/api/projects/{pid}").json()["status"] == "edited"

    dr = _run(client, f"/api/projects/{pid}/render?final=false")
    assert dr["result"]["kind"] == "draft"
    assert client.get(f"/api/projects/{pid}").json()["status"] == "draft_rendered"
    renders = client.get(f"/api/projects/{pid}/renders").json()
    draft = next(a for a in renders if a["kind"] == "draft")
    assert draft["meta"]["resolution"] == "480p"
    assert draft["content_type"] == "video/mp4"
    assert client.get(draft["url"]).content[4:8] == b"ftyp"  # playable mp4

    fr = _run(client, f"/api/projects/{pid}/render?final=true")
    assert fr["result"]["kind"] == "final"
    assert client.get(f"/api/projects/{pid}").json()["status"] == "rendered"
    final = next(a for a in client.get(f"/api/projects/{pid}/renders").json() if a["kind"] == "final")
    assert final["meta"]["resolution"] == "1080p"


def test_edl_requires_clips(client):
    p = _make_project(client, target_length=15)
    _storyboard(client, p["id"])
    assert client.post(f"/api/projects/{p['id']}/edl").status_code == 400


def test_render_requires_edl(client):
    p = _make_project(client, target_length=15)
    _storyboard(client, p["id"])
    assert client.post(f"/api/projects/{p['id']}/render").status_code == 400
    assert client.get(f"/api/projects/{p['id']}/edl").status_code == 404


def test_final_render_regenerates_hero_scenes(client):
    p = _edit_ready(client)
    pid = p["id"]
    sid = client.get(f"/api/projects/{pid}/scenes").json()[0]["id"]
    client.patch(f"/api/projects/{pid}/scenes/{sid}",
                 json={"audio_mode": "dialogue", "dialogue_text": "We made it."})
    _run(client, f"/api/projects/{pid}/edl")
    fr = _run(client, f"/api/projects/{pid}/render?final=true")
    assert fr["result"]["regenerated"] >= 1  # the dialogue scene is a hero shot


def test_render_replaces_previous_of_same_tier(client):
    p = _edit_ready(client)
    pid = p["id"]
    _run(client, f"/api/projects/{pid}/edl")
    _run(client, f"/api/projects/{pid}/render?final=false")
    first = next(a for a in client.get(f"/api/projects/{pid}/renders").json() if a["kind"] == "draft")
    _run(client, f"/api/projects/{pid}/render?final=false")
    drafts = [a for a in client.get(f"/api/projects/{pid}/renders").json() if a["kind"] == "draft"]
    assert len(drafts) == 1 and drafts[0]["id"] != first["id"]  # replaced, not duplicated


def test_music_upload_and_remove(client):
    from app.pipeline import mock
    p = _make_project(client, target_length=15)
    _storyboard(client, p["id"])
    pid = p["id"]
    wav = mock.silent_wav(2.0)
    r = client.post(f"/api/projects/{pid}/music",
                    files={"file": ("bed.wav", wav, "audio/wav")})
    assert r.status_code == 201
    assert "beat_grid" in r.json()["meta"]
    assert client.get(f"/api/projects/{pid}/music").json() is not None
    assert client.delete(f"/api/projects/{pid}/music").status_code == 204
    assert client.get(f"/api/projects/{pid}/music").json() is None
