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


def test_premium_tier_routes_premium_model(client):
    p = _ready_project(client)
    pid = p["id"]
    _video(client, pid, tier="premium")
    sid = client.get(f"/api/projects/{pid}/scenes").json()[0]["id"]
    clip_id = client.get(f"/api/projects/{pid}/scenes/{sid}").json()["clip_asset_id"]
    clip = client.get(f"/api/assets/{clip_id}").json()
    assert clip["meta"]["model_id"] == "kling-3-pro"  # premium narrated default
