"""One-off: regenerate a fixed list of scenes' clips, sequentially.

The project allows only one generation job at a time, so we enqueue per scene
and wait for each job to reach a terminal state before the next. Live mode will
make real provider (Kling) calls.
"""
from __future__ import annotations

import json
import sys
import time
import urllib.request

BASE = "http://localhost:8800"
PID = "6538420a-d88f-4eb5-ac45-957993ce6798"
SCENES = [
    (1, "e598d03e-9633-4a70-ad88-52c25f44bd6e"), (2, "04b8cc61-a62c-459d-9042-562d499a5767"),
    (3, "c43b1397-5fff-461b-a07c-527fb2416717"), (4, "0a931642-d9ce-4bc9-a02b-f47cbed5d829"),
    (5, "000aacf7-34a3-4e8c-9e84-a9205620291c"), (6, "3bd0ce3c-799a-4fb8-9842-993ab327484d"),
    (7, "11653f05-ed30-44ed-a9da-1dffa6256c87"), (8, "d1ddf54a-8b33-4ac7-bb30-261e847acd4a"),
    (9, "12b84b4e-3b5c-4888-b395-cc0217e3a13e"), (10, "799c12d3-8787-4dfe-9e71-8df9653460a5"),
    (11, "cc1c5f8e-1268-4827-bb28-f5b9b32210d7"), (12, "227d6435-8f66-4ff7-9197-65b5ebcb02b6"),
    (13, "8e535a31-a666-4e0a-8fef-cf407d7a5466"), (14, "d933fddc-1bf3-4f33-a57c-0f5d233c0a2c"),
    (15, "509fbb54-b97f-4c7c-9d05-1cd069193532"), (16, "32a609c1-35e5-4fb8-82df-8edfcfb9abc2"),
    (17, "103eba64-bf67-4805-b8d0-949c2c16c205"), (18, "c5308a40-5cff-48f1-a074-1dda34fd6966"),
    (19, "8768f4c0-7164-4337-be50-4b3d9399b31f"),
]


def _req(method, path):
    r = urllib.request.Request(BASE + path, method=method)
    with urllib.request.urlopen(r, timeout=30) as resp:
        return json.loads(resp.read().decode())


def main():
    ok = bad = 0
    for num, sid in SCENES:
        try:
            job = _req("POST", f"/api/projects/{PID}/scenes/{sid}/video?tier=draft")
            jid = job["id"]
        except Exception as exc:  # noqa: BLE001
            print(f"scene {num:2}: ENQUEUE FAILED {exc}", flush=True)
            bad += 1
            continue
        print(f"scene {num:2}: job {jid[:8]} enqueued…", flush=True)
        status = "queued"
        while status in ("queued", "running"):
            time.sleep(5)
            try:
                j = _req("GET", f"/api/jobs/{jid}")
                status = j.get("status", "?")
            except Exception as exc:  # noqa: BLE001
                status = f"poll-error:{exc}"
                break
        if status == "success":
            ok += 1
        else:
            bad += 1
        print(f"scene {num:2}: -> {status}", flush=True)
    print(f"DONE  ok={ok}  failed/other={bad}", flush=True)
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
