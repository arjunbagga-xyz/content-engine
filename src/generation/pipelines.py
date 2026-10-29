"""Pipeline registry: maps a pipeline name (declared per-account in
characters.yaml) to its producer function.

Each producer has the uniform signature:
    await producer(account_id, output_path, topic=None, num_turns=None) -> dict
and returns at least {"path": str, "topic": str, "turns": list, ...}.

Accounts declare which pipelines they use (and with what weight) in YAML:
    pipelines: [debate_2lead_cameo, roundtable]
    pipeline_weights: {debate_2lead_cameo: 0.35, roundtable: 0.65}

The scheduler picks a pipeline (weighted random) per reel and calls it.
Adding a new pipeline = register it here + implement the producer. No
scheduler changes needed.
"""
from __future__ import annotations
import random
from typing import Awaitable, Callable, Dict, List

from src.generation import sprite_reactor as SR

# producer signature: async (account_id, output_path, topic=None, num_turns=None) -> dict
Producer = Callable[..., Awaitable[dict]]

PIPELINE_REGISTRY: Dict[str, Producer] = {
    "debate_2lead_cameo": SR.SpriteReactor.produce_account_debate,
    "roundtable": SR.SpriteReactor.produce_roundtable,
    "chat_roast": lambda acct, out, topic=None, num_turns=None: _run_chat_roast(acct, out, topic, num_turns),
    "x_comments": lambda acct, out, topic=None, num_turns=None: _run_x_comments(acct, out, topic, num_turns),
    "phone_call": lambda acct, out, topic=None, num_turns=None: _run_phone_call(acct, out, topic, num_turns),
    "storytime": lambda acct, out, topic=None, num_turns=None: _run_storytime(acct, out, topic, num_turns),
}


async def _run_chat_roast(acct, out, topic, num_turns, tone_plan=None):
    from src.generation.pipeline_impl.chat_roast import run_pipeline
    result = await run_pipeline(acct, out, topic=topic, num_turns=num_turns, tone_plan=tone_plan)
    return {"path": result, "topic": topic, "turns": []}


async def _run_x_comments(acct, out, topic, num_turns, tone_plan=None):
    from src.generation.pipeline_impl.x_comments import run_pipeline
    result = await run_pipeline(acct, out, topic=topic, num_turns=num_turns, tone_plan=tone_plan)
    return {"path": result, "topic": topic, "turns": []}


async def _run_phone_call(acct, out, topic, num_turns, tone_plan=None):
    from src.generation.pipeline_impl.phone_call import run_pipeline
    result = await run_pipeline(acct, out, topic=topic, num_turns=num_turns, tone_plan=tone_plan)
    return {"path": result, "topic": topic, "turns": []}


async def _run_storytime(acct, out, topic, num_turns):
    from src.generation.pipeline_impl.storytime import run_pipeline
    chars = _get_chars(acct)
    result = run_pipeline(chars[0], topic or "fitness", out, paragraphs=num_turns or 4)
    return {"path": result, "topic": topic, "turns": []}


def _get_chars(acct):
    """Extract characters from account config."""
    chars = acct.get("characters", [])
    if len(chars) < 2:
        return [
            {"name": "Goku", "persona": "cheerful naive", "tts_voice": "en-US-GuyNeural"},
            {"name": "Vegeta", "persona": "proud angry", "tts_voice": "en-US-SteffanNeural"}
        ]
    return [
        {"name": chars[0], "persona": "character", "tts_voice": "en-US-GuyNeural"},
        {"name": chars[1], "persona": "character", "tts_voice": "en-US-SteffanNeural"}
    ]


def available_pipelines() -> List[str]:
    return list(PIPELINE_REGISTRY.keys())


def select_pipeline(account_conf: dict) -> str:
    """Weighted-random pick of a pipeline for this account.

    Reads `pipelines` (list) and optional `pipeline_weights` (dict) from the
    account config. Falls back to the first declared pipeline, then to any
    registered pipeline.
    """
    declared = account_conf.get("pipelines") or list(PIPELINE_REGISTRY.keys())
    # keep only registered ones
    declared = [p for p in declared if p in PIPELINE_REGISTRY]
    if not declared:
        declared = list(PIPELINE_REGISTRY.keys())
    weights = account_conf.get("pipeline_weights") or {}
    w = [float(weights.get(p, 1.0)) for p in declared]
    total = sum(w) or 1.0
    w = [x / total for x in w]
    return random.choices(declared, weights=w, k=1)[0]


def get_producer(name: str) -> Producer:
    if name not in PIPELINE_REGISTRY:
        raise ValueError(f"Unknown pipeline '{name}'. Registered: {available_pipelines()}")
    return PIPELINE_REGISTRY[name]
