"""Config-driven LLM routing table.

Same idea as `models_config` for video models: the pipeline references LLMs by a
stable internal id, and a project picks which one to use ("change based on the
prompt"). Adding a model is a config edit here, not a refactor.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LLMRoute:
    id: str
    label: str
    provider: str   # "openai" | "anthropic"
    model: str      # the provider-specific model id sent to the API
    vision: bool     # supports image inputs (keyframe ranking / quality / EDL)


LLM_ROUTES: dict[str, LLMRoute] = {
    "gpt-5.4-nano": LLMRoute(
        id="gpt-5.4-nano", label="GPT-5.4 nano", provider="openai",
        model="gpt-5.4-nano", vision=True,
    ),
    "claude-haiku-4-6": LLMRoute(
        id="claude-haiku-4-6", label="Claude Haiku 4.6", provider="anthropic",
        model="claude-haiku-4-6", vision=True,
    ),
}

DEFAULT_LLM = "gpt-5.4-nano"


def llm_route(llm_id: str | None) -> LLMRoute:
    """Resolve an LLM id (or None → default) to its route."""
    if not llm_id or llm_id not in LLM_ROUTES:
        llm_id = DEFAULT_LLM
    return LLM_ROUTES[llm_id]


def is_known(llm_id: str | None) -> bool:
    return bool(llm_id) and llm_id in LLM_ROUTES
