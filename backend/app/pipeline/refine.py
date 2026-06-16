"""Stage: multi-agent storyboard + narration critique/refine (CrewAI).

A small crew of role agents critiques the draft storyboard, and a Showrunner agent
emits a corrected storyboard as STRICT JSON (same schema as the storyboard stage).
User-triggered ("Refine with AI" on the review page), so it runs only on demand.

Mock-gated: under MOCK_GENERATION the crew never runs (zero spend) — we return the
storyboard unchanged with a marker. CrewAI is imported lazily inside the real branch
so mock mode + the test suite don't require the package. A crew run that produces
unparseable/invalid output falls back to the original storyboard, so refining can
never corrupt a good storyboard.
"""
from __future__ import annotations

import json
import logging

from ..config import settings
from ..llm import _extract_json
from ..llm_config import llm_route

log = logging.getLogger("storyforge")

# Specialist agents the crew can include (the Showrunner orchestrator is always on).
ALL_AGENTS = ("cinematographer", "continuity", "music_director", "fact_checker")

# Music-bed ids the Music Director may recommend (kept in sync with audio.MUSIC_LIBRARY).
_MUSIC_IDS = ("ambient-80", "cinematic-100", "upbeat-128")


def _crew_llm(llm_id: str | None):
    """Map the project's LLM route to a CrewAI/litellm LLM using our own keys."""
    from crewai import LLM

    route = llm_route(llm_id)
    if route.provider == "openai":
        if not settings.openai_api_key:
            raise RuntimeError("OPENAI_API_KEY not set (and mock mode is off).")
        return LLM(model=route.model, api_key=settings.openai_api_key)
    if route.provider == "anthropic":
        if not settings.anthropic_api_key:
            raise RuntimeError("ANTHROPIC_API_KEY not set (and mock mode is off).")
        return LLM(model=f"anthropic/{route.model}", api_key=settings.anthropic_api_key)
    raise RuntimeError(f"unsupported LLM provider for refine crew: {route.provider!r}")


def refine_storyboard(
    *, idea: str, target_length: int, style_bible: dict | None, storyboard: dict,
    llm: str | None = None, agents: list[str] | None = None,
) -> dict:
    """Critique + refine the storyboard via the agent crew.

    Returns a storyboard dict ({"scenes": [...]}) plus an optional "music_suggestion".
    In mock mode (or on any failure) returns the input storyboard unchanged.
    """
    if settings.mock_generation:
        out = {"scenes": [dict(s) for s in storyboard.get("scenes", [])]}
        out["_refined_mock"] = True
        return out

    enabled = set(agents or ALL_AGENTS)
    try:
        return _run_crew(
            idea=idea, target_length=target_length, style_bible=style_bible,
            storyboard=storyboard, llm=llm, enabled=enabled,
        )
    except Exception:  # noqa: BLE001 — never let a crew failure corrupt the storyboard
        log.exception("refine crew failed; keeping the original storyboard")
        return {"scenes": [dict(s) for s in storyboard.get("scenes", [])]}


def _run_crew(*, idea, target_length, style_bible, storyboard, llm, enabled: set[str]) -> dict:
    from crewai import Agent, Crew, Process, Task

    crew_llm = _crew_llm(llm)
    sb_json = json.dumps(storyboard, indent=2)
    style_json = json.dumps(style_bible or {}, indent=2)
    ctx = (
        f"IDEA: {idea}\nTARGET LENGTH: {target_length} seconds\n"
        f"STYLE BIBLE:\n{style_json}\n\nCURRENT STORYBOARD JSON:\n{sb_json}"
    )

    def agent(role, goal, backstory):
        return Agent(role=role, goal=goal, backstory=backstory, llm=crew_llm,
                     allow_delegation=False, verbose=False)

    # --- Core specialists (always on) ---
    tasks, agents = [], []
    story_editor = agent(
        "Story Editor",
        "Critique narrative arc, hook, pacing, and scene-to-scene coherence.",
        "A veteran short-film editor who cuts anything that doesn't earn its place.",
    )
    tasks.append(Task(
        description=f"{ctx}\n\nCritique the story: arc, hook, pacing, redundant or weak "
                    f"beats. Give concrete per-scene fixes. Do NOT output JSON.",
        expected_output="A concise bullet critique with per-scene fixes.",
        agent=story_editor,
    ))
    agents.append(story_editor)

    narration_writer = agent(
        "Narration Writer",
        "Rewrite narration into one cohesive voiceover paced to the film length.",
        "A documentary VO writer. All scenes' narration_text is concatenated into ONE "
        "continuous track (not timed per scene), so it must flow start to finish at "
        "~2.5 spoken words per second of TOTAL duration.",
    )
    tasks.append(Task(
        description=f"{ctx}\n\nRewrite every scene's narration_text so the concatenation "
                    f"reads as one flowing voiceover, paced to ~2.5 words/sec of the "
                    f"{target_length}s target. Return the new narration per scene number.",
        expected_output="New narration_text per scene number.",
        agent=narration_writer,
    ))
    agents.append(narration_writer)

    # --- Optional specialists ---
    if "cinematographer" in enabled:
        a = agent(
            "Cinematographer",
            "Strengthen image_prompt/video_prompt/camera_movement and shot variety.",
            "A DP who ensures the locked style bible is embedded verbatim in every prompt "
            "and that shots vary (no repeated framings).",
        )
        tasks.append(Task(
            description=f"{ctx}\n\nImprove image_prompt, video_prompt and camera_movement "
                        f"per scene; ensure shot variety and that style descriptors are "
                        f"embedded. Keep prompts concise. Return changes per scene number.",
            expected_output="Improved prompts per scene number.", agent=a))
        agents.append(a)

    if "continuity" in enabled:
        a = agent(
            "Continuity Supervisor",
            "Enforce character/style consistency, contiguous scene numbers, and that "
            f"durations sum to ~{target_length}s (each scene 2-8s).",
            "A continuity supervisor who catches drift in character descriptors and timing.",
        )
        tasks.append(Task(
            description=f"{ctx}\n\nFlag continuity issues (character/style drift, numbering) "
                        f"and fix durations so they sum within 2s of {target_length}s, each "
                        f"scene 2-8s. Return corrected durations + continuity fixes.",
            expected_output="Continuity fixes and corrected per-scene durations.", agent=a))
        agents.append(a)

    if "fact_checker" in enabled:
        a = agent(
            "Fact Checker",
            "Catch factual errors in the narration for real-subject films.",
            "A meticulous researcher. If the subject is fictional, say so and pass.",
        )
        tasks.append(Task(
            description=f"{ctx}\n\nCheck the narration for factual errors about the subject. "
                        f"List corrections, or state the subject is fictional.",
            expected_output="Factual corrections, or 'fictional — no changes'.", agent=a))
        agents.append(a)

    music_note = ""
    if "music_director" in enabled:
        a = agent(
            "Music Director",
            f"Recommend ONE music bed id from {list(_MUSIC_IDS)} matching the mood.",
            "A film composer who matches a bed's energy to the piece.",
        )
        tasks.append(Task(
            description=f"{ctx}\n\nPick the single best music bed id from {list(_MUSIC_IDS)} "
                        f"for this film's mood. Answer with just the id and a one-line reason.",
            expected_output="One music bed id + one-line reason.", agent=a))
        agents.append(a)
        music_note = (f" Also include a top-level \"music_suggestion\" field set to the "
                      f"Music Director's chosen id (one of {list(_MUSIC_IDS)}).")

    # --- Showrunner: synthesize all critiques into the final corrected storyboard JSON ---
    showrunner = agent(
        "Showrunner",
        "Apply every specialist's notes and emit the final corrected storyboard JSON.",
        "The director of record. Output is consumed by code, so it must be valid JSON.",
    )
    showrunner_task = Task(
        description=(
            f"{ctx}\n\nApply the specialists' notes above and output the FINAL corrected "
            f"storyboard as STRICT JSON with the SAME shape as the input (a top-level "
            f"\"scenes\" array; each scene keeps scene_number, duration_seconds, "
            f"shot_description, camera_movement, image_prompt, video_prompt, narration_text, "
            f"audio_mode='narrated', dialogue_text=null, suggested_model). Keep scene_numbers "
            f"contiguous from 1 and durations within 2s of {target_length}s.{music_note} "
            f"Output ONLY the JSON, no prose."
        ),
        expected_output="The complete corrected storyboard as strict JSON.",
        agent=showrunner,
        context=tasks[:],  # all specialist critiques feed the showrunner
    )
    agents.append(showrunner)
    tasks.append(showrunner_task)

    crew = Crew(agents=agents, tasks=tasks, process=Process.sequential, verbose=False)
    result = crew.kickoff()
    raw = getattr(result, "raw", None) or str(result)
    refined = _extract_json(raw)
    if not isinstance(refined, dict) or "scenes" not in refined:
        raise ValueError("crew did not return a storyboard with a 'scenes' array")
    return refined
