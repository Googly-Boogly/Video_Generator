"""Live API smoke test — exercises every endpoint against a running stack.

Usage:  python scripts/smoke_test.py   (defaults to http://localhost:8800)
Set BASE env var to point elsewhere. Requires the stack to be up and in MOCK mode.
"""
import os
import json, time, urllib.request, urllib.error

BASE = os.environ.get("BASE", "http://localhost:8800")
passed = failed = 0

def call(method, path, body=None, expect=None):
    url = BASE + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req) as r:
            code = r.status
            txt = r.read().decode()
    except urllib.error.HTTPError as e:
        code = e.code
        txt = e.read().decode()
    out = json.loads(txt) if txt and txt[0] in "{[" else txt
    return code, out

def check(name, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1; print(f"  PASS  {name}")
    else:
        failed += 1; print(f"  FAIL  {name}  {detail}")

def poll(job_id, timeout=15):
    for _ in range(int(timeout/0.4)):
        c, j = call("GET", f"/api/jobs/{job_id}")
        if j["status"] in ("success", "failed"):
            return j
        time.sleep(0.4)
    return {"status": "timeout"}

print("== health & config ==")
c, h = call("GET", "/health"); check("health 200 + mock", c==200 and h["mock_generation"] is True, h)
c, cfg = call("GET", "/api/config"); check("config 200", c==200)
check("config has 7 models", len(cfg["models"])==7, len(cfg["models"]))
check("config video_models", "kling-3-pro" in cfg["video_models"])
check("style presets present", len(cfg["style_presets"])>=5)

print("== validation errors ==")
c, _ = call("POST", "/api/projects", {"idea":"x"}); check("short idea rejected (422)", c==422, c)
c, _ = call("POST", "/api/projects", {"idea":"valid idea here","target_length":45}); check("bad length rejected (422)", c==422, c)
c, _ = call("GET", "/api/projects/does-not-exist"); check("missing project 404", c==404, c)
c, _ = call("GET", "/api/jobs/nope"); check("missing job 404", c==404, c)

print("== create + storyboard lifecycle ==")
c, p = call("POST", "/api/projects", {"idea":"A clockmaker who repairs memories","target_length":15,"aspect_ratio":"9:16","style_preset":"noir"})
check("create 201", c==201, c); pid = p["id"]
check("initial status draft", p["status"]=="draft", p["status"])
c, job = call("POST", f"/api/projects/{pid}/storyboard"); check("kickoff 202", c==202, c)
j = poll(job["id"]); check("storyboard job success", j["status"]=="success", j)
c, p = call("GET", f"/api/projects/{pid}")
check("status -> storyboarded", p["status"]=="storyboarded", p["status"])
check("style_bible present", p.get("style_bible") is not None)
check("9:16 board has scenes", len(p["scenes"])>=3, len(p["scenes"]))
check("15s target fits", abs(sum(s["duration_seconds"] for s in p["scenes"]) - 15) <= 6)
check("scene_numbers contiguous", [s["scene_number"] for s in p["scenes"]]==list(range(1,len(p["scenes"])+1)))
check("every scene routes to known model", all(s["suggested_model"] in {m["id"] for m in cfg["models"]} for s in p["scenes"]))

print("== scene editing ==")
scenes = p["scenes"]; s1 = scenes[0]["id"]
c, s = call("PATCH", f"/api/projects/{pid}/scenes/{s1}", {"shot_description":"EDITED desc","duration_seconds":4})
check("patch applied", s["shot_description"]=="EDITED desc" and s["duration_seconds"]==4.0, s)
c, s = call("PATCH", f"/api/projects/{pid}/scenes/{s1}", {"audio_mode":"dialogue","dialogue_text":"tick tock"})
check("dialogue auto-routes to lip-sync model", cfg["models"] and s["suggested_model"]=="veo-31", s["suggested_model"])
c, s = call("PATCH", f"/api/projects/{pid}/scenes/{s1}", {"model_override":"bogus-model"})
check("bad model_override rejected 400", c==400, c)
c, s = call("PATCH", f"/api/projects/{pid}/scenes/{s1}", {"model_override":"seedance-2"})
check("valid override accepted", s["model_override"]=="seedance-2", s)

print("== reorder / add / delete ==")
ids = [s["id"] for s in scenes]
c, ro = call("POST", f"/api/projects/{pid}/scenes/reorder", {"scene_ids": list(reversed(ids))})
check("reorder 200 + renumbered", c==200 and [x["scene_number"] for x in ro]==list(range(1,len(ro)+1)), c)
c, _ = call("POST", f"/api/projects/{pid}/scenes/reorder", {"scene_ids": ids[:-1]})
check("reorder w/ wrong set rejected 400", c==400, c)
n0 = len(ro)
c, ns = call("POST", f"/api/projects/{pid}/scenes", {"after_scene_number": 1})
check("add scene 201", c==201, c)
c, lst = call("GET", f"/api/projects/{pid}/scenes")
check("count +1 after add", len(lst)==n0+1, len(lst))
check("contiguous after add", [s["scene_number"] for s in lst]==list(range(1,len(lst)+1)))
del_id = lst[-1]["id"]
c, _ = call("DELETE", f"/api/projects/{pid}/scenes/{del_id}")
check("delete 204", c==204, c)
c, lst = call("GET", f"/api/projects/{pid}/scenes")
check("count -1 + contiguous after delete", len(lst)==n0 and [s["scene_number"] for s in lst]==list(range(1,len(lst)+1)))

print("== conversational revision ==")
c, rj = call("POST", f"/api/projects/{pid}/scenes/revise", {"instruction":"make scene 2 moodier"})
check("revise 202", c==202, c)
j = poll(rj["id"]); check("revise job success", j["status"]=="success", j)
c, lst = call("GET", f"/api/projects/{pid}/scenes")
check("scene 2 revised", "revised" in lst[1]["shot_description"], lst[1]["shot_description"][-40:])
c, rj = call("POST", f"/api/projects/{pid}/scenes/revise", {"instruction":"x"})
check("short instruction rejected 422", c==422, c)

print("== cost estimator ==")
c, cost_p = call("GET", f"/api/projects/{pid}/cost?tier=premium")
c2, cost_d = call("GET", f"/api/projects/{pid}/cost?tier=draft")
check("premium cost > draft cost", cost_p["total"] > cost_d["total"], (cost_p["total"], cost_d["total"]))
check("cost has line items", len(cost_p["line_items"])==3)

print("== keyframes (best-of-N) ==")
c, _ = call("GET", f"/api/projects/{pid}")  # ensure scenes present
c, kj = call("POST", f"/api/projects/{pid}/keyframes"); check("keyframes kickoff 202", c==202, c)
j = poll(kj["id"], timeout=30); check("keyframes job success", j["status"]=="success", j)
c, refs = call("GET", f"/api/projects/{pid}/references")
check("3+ reference images w/ roles", len(refs) >= 3 and refs[0]["meta"]["role"]=="character", refs and refs[0]["meta"])
c, proj = call("GET", f"/api/projects/{pid}")
check("project status -> keyframes", proj["status"]=="keyframes", proj["status"])
sid0 = proj["scenes"][0]["id"]
check("scene done + has winner", proj["scenes"][0]["status"]=="done" and proj["scenes"][0]["keyframe_asset_id"])
c, kfs = call("GET", f"/api/projects/{pid}/scenes/{sid0}/keyframes")
check("3 variants per scene", len(kfs)==3, len(kfs))
check("exactly one auto-winner", sum(1 for k in kfs if k["meta"]["is_winner"])==1)
# asset content proxy serves real image bytes
import urllib.request as _u
with _u.urlopen(BASE + kfs[0]["url"]) as r:
    head = r.read(4); ctype = r.headers.get("content-type","")
check("asset content is an image", ctype.startswith("image/") and head==b"\x89PNG", (ctype, head))
# user override of winner
runner = next(k for k in kfs if not k["meta"]["is_winner"])
c, s = call("POST", f"/api/projects/{pid}/scenes/{sid0}/keyframe/select", {"asset_id": runner["id"]})
check("select override applied", s["keyframe_asset_id"]==runner["id"], c)
# single-scene regenerate -> fresh assets
old_ids = {k["id"] for k in kfs}
c, rkj = call("POST", f"/api/projects/{pid}/scenes/{sid0}/keyframes"); poll(rkj["id"], timeout=20)
c, kfs2 = call("GET", f"/api/projects/{pid}/scenes/{sid0}/keyframes")
check("regenerate yields fresh variants", len(kfs2)==3 and old_ids.isdisjoint({k["id"] for k in kfs2}))

print("== video generation + quality gate ==")
c, vj = call("POST", f"/api/projects/{pid}/video?tier=draft"); check("video kickoff 202", c==202, c)
j = poll(vj["id"], timeout=90); check("video job success", j["status"]=="success", j)
check("clips produced", j["result"]["scenes_done"] >= 1, j["result"])
c, proj = call("GET", f"/api/projects/{pid}")
check("project status -> clips", proj["status"]=="clips", proj["status"])
s0 = proj["scenes"][0]
check("scene has clip + native audio", bool(s0["clip_asset_id"]) and bool(s0["native_audio_asset_id"]))
check("scene has quality verdict", s0["quality"] is not None and "flagged" in s0["quality"])
c, clip = call("GET", f"/api/assets/{s0['clip_asset_id']}")
check("clip is video/mp4", clip["content_type"]=="video/mp4", clip["content_type"])
with _u.urlopen(BASE + clip["url"]) as r:
    head = r.read(8)
check("clip bytes are a real mp4", head[4:8]==b"ftyp", head)
c, frames = call("GET", f"/api/projects/{pid}/scenes/{s0['id']}/frames")
check("4 quality-gate frames", len(frames)==4, len(frames))
# one-click regenerate of a single scene's clip
old_clip = s0["clip_asset_id"]
c, rvj = call("POST", f"/api/projects/{pid}/scenes/{s0['id']}/video"); poll(rvj["id"], timeout=60)
c, s0b = call("GET", f"/api/projects/{pid}/scenes/{s0['id']}")
check("regenerate yields a fresh clip", s0b["clip_asset_id"] and s0b["clip_asset_id"] != old_clip)

print("== audio: catalogs, voice, music + beat grid ==")
c, voices = call("GET", "/api/voices"); check("voices catalog", c==200 and len(voices["voices"])>=3, c)
c, lib = call("GET", "/api/music/library"); check("music library", c==200 and len(lib["tracks"])>=3, c)
c, _ = call("POST", f"/api/projects/{pid}/voice", {"voice_id":"bogus"}); check("bad voice rejected 400", c==400, c)
c, v = call("POST", f"/api/projects/{pid}/voice", {"voice_id":"voice_atlas"}); check("set voice", v.get("voice_id")=="voice_atlas", v)
c, m = call("POST", f"/api/projects/{pid}/music/library", {"track_id":"upbeat-128"}); check("pick library music 201", c==201, c)
grid = m["meta"]["beat_grid"]
check("librosa detected a beat grid", grid["engine"]=="librosa" and grid["bpm"]>0 and len(grid["beats"])>5, grid.get("engine"))

print("== audio: build narration + rebuild ==")
c, aj = call("POST", f"/api/projects/{pid}/audio"); check("audio build 202", c==202, c)
j = poll(aj["id"], timeout=60); check("audio job success", j["status"]=="success", j)
check("narration produced", j["result"]["narrated"]>=1, j["result"])
c, narr = call("GET", f"/api/projects/{pid}/narration")
check("narration assets match", len(narr)==j["result"]["narrated"], len(narr))
check("narration has voice + duration", bool(narr[0]["meta"]["voice_id"]) and narr[0]["meta"]["duration"]>0)
c, proj = call("GET", f"/api/projects/{pid}"); check("project status -> audio", proj["status"]=="audio", proj["status"])
# rebuild must not trip the delete-orphan cascade nor duplicate narration
c, aj2 = call("POST", f"/api/projects/{pid}/audio"); j2 = poll(aj2["id"], timeout=60)
check("audio rebuild success (no cascade)", j2["status"]=="success", j2)
c, narr2 = call("GET", f"/api/projects/{pid}/narration")
check("rebuild does not duplicate narration", len(narr2)==len(narr), (len(narr), len(narr2)))
c, mp = call("GET", f"/api/projects/{pid}/mix-plan")
check("mix plan levels present", mp["levels"]["native_db"]==-16.0 and mp["levels"]["music_db"]==-18.0, mp.get("levels"))

print("== AI editor: build EDL ==")
c, ej = call("POST", f"/api/projects/{pid}/edl"); check("EDL build 202", c==202, c)
j = poll(ej["id"], timeout=60); check("EDL job success", j["status"]=="success", j)
c, edl = call("GET", f"/api/projects/{pid}/edl")
check("EDL has cuts + duration", len(edl["cuts"])>=1 and edl["total_duration"]>0, edl.get("total_duration"))
check("EDL cut has trim + mix", edl["cuts"][0]["trim_head"]>0 and "mix" in edl["cuts"][0])
c, proj = call("GET", f"/api/projects/{pid}"); check("project status -> edited", proj["status"]=="edited", proj["status"])

print("== render: draft (480p) then final (1080p) ==")
c, dj = call("POST", f"/api/projects/{pid}/render?final=false"); check("draft render 202", c==202, c)
j = poll(dj["id"], timeout=120); check("draft render success", j["status"]=="success" and j["result"]["kind"]=="draft", j)
c, proj = call("GET", f"/api/projects/{pid}"); check("project status -> draft_rendered", proj["status"]=="draft_rendered", proj["status"])
c, renders = call("GET", f"/api/projects/{pid}/renders")
draft = next(a for a in renders if a["kind"]=="draft")
check("draft is 480p mp4", draft["meta"]["resolution"]=="480p" and draft["content_type"]=="video/mp4", draft["meta"])
with _u.urlopen(BASE + draft["url"]) as r: head = r.read(8)
check("draft is a real mp4", head[4:8]==b"ftyp", head)
c, fj = call("POST", f"/api/projects/{pid}/render?final=true"); check("final render 202", c==202, c)
j = poll(fj["id"], timeout=150); check("final render success", j["status"]=="success" and j["result"]["kind"]=="final", j)
c, proj = call("GET", f"/api/projects/{pid}"); check("project status -> rendered", proj["status"]=="rendered", proj["status"])
c, renders = call("GET", f"/api/projects/{pid}/renders")
final = next(a for a in renders if a["kind"]=="final")
check("final is 1080p", final["meta"]["resolution"]=="1080p", final["meta"])
# download header
import urllib.request as _u2
req = _u2.Request(BASE + final["url"] + "?download=1")
with _u2.urlopen(req) as r: disp = r.headers.get("Content-Disposition","")
check("export sets download header", "attachment" in disp, disp)

print("== cost dashboard (estimated vs actual ledger) ==")
c, cd = call("GET", f"/api/projects/{pid}/costs"); check("cost dashboard 200", c==200, c)
check("estimated total > 0", cd["estimated"]["total"] > 0, cd["estimated"]["total"])
check("actual ledger total > 0", cd["actual"]["total"] > 0, cd["actual"]["total"])
check("ledger has keyframes+video+audio+render steps",
      all(k in cd["actual"]["by_step"] for k in ("keyframes","video","audio")), cd["actual"]["by_step"])
check("by_step sums to actual total", abs(sum(cd["actual"]["by_step"].values()) - cd["actual"]["total"]) < 1e-6)
check("entries flagged mock", cd["mock"] is True and cd["actual"]["entries"][0]["mock"] is True)

print("== jobs listing ==")
c, jobs = call("GET", f"/api/jobs/project/{pid}")
check("jobs for project (>=9)", c==200 and len(jobs)>=9, len(jobs))

print("== cleanup ==")
c, _ = call("DELETE", f"/api/projects/{pid}")
check("delete project 204", c==204, c)
c, _ = call("GET", f"/api/projects/{pid}")
check("project gone 404", c==404, c)

print(f"\n==== {passed} passed, {failed} failed ====")
import sys; sys.exit(1 if failed else 0)
