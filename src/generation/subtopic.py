"""Subtopic resolver — turns a BROAD theme into a CONCRETE, specific, timely topic.

Why this exists:
  The old pipeline did `random.choice(topics)` from a static list ("science",
  "geopolitics"...) and fed that to the writer. Result: generic debate scripts
  ("is science good? yes/no"). We now use the LLM to resolve a broad theme into a
  sharp, concrete, current subtopic (e.g. "the recent gluon discovery inside the
  proton and what it implies for the Standard Model of particle physics") so the
  characters debate SOMETHING real.

Bleed prevention (explicit, per user requirement):
  The LLM returns ONLY a plain topic sentence as DATA. The system prompt forbids
  instruction-language, meta-words ("topic"/"trend"/"angle"/"brief"), and any
  script text. The writer receives the topic as data and is told never to echo it.
  Every call logs INPUT -> OUTPUT to logs/subtopic_io.log so it is inspectable.

No daemon, no scheduling — this is a pure function called by the planner at
plan time (the planner decides topics up front, logs them, and they are stored
on the ContentPost.topic column).
"""
import json
import logging
import datetime
import os

from src.llm.router import router, TaskType

logger = logging.getLogger("content_engine.subtopic")

_IO_LOG = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "logs", "subtopic_io.log")


def _log_io(theme: str, role: str, account_id: str, output: str, ok: bool, err: str = ""):
    try:
        os.makedirs(os.path.dirname(_IO_LOG), exist_ok=True)
        ts = datetime.datetime.utcnow().isoformat()
        with open(_IO_LOG, "a", encoding="utf-8") as f:
            f.write(f"\n=== {ts} account={account_id} ok={ok} ===\n")
            f.write(f"INPUT theme : {theme}\n")
            f.write(f"INPUT role : {role}\n")
            f.write(f"OUTPUT      : {output}\n")
            if err:
                f.write(f"ERROR       : {err}\n")
    except Exception:
        pass


_SYSTEM = (
    "You are a sharp current-affairs researcher for a debate show. Given a broad "
    "theme and the account's persona, propose ONE concrete, specific, and timely "
    "subtopic the two characters could genuinely debate.\n"
    "Rules (strict):\n"
    "1. Return ONLY a single plain sentence naming the subtopic. No preamble.\n"
    "2. Never use meta-words: no 'topic', 'trend', 'angle', 'brief', 'subtopic', "
    "'today we discuss'. Just the subject.\n"
    "3. Be concrete and specific: name the discovery, event, study, law, or number. "
    "Not 'science is good' — e.g. 'the recent gluon-discovery inside the proton and "
    "what it means for the Standard Model'.\n"
    "4. Make it genuinely debatable from two opposing worldviews (not a fact both "
    "agree on).\n"
    "5. Do NOT write the script, dialogue, or any character lines. Only the topic.\n"
)


async def resolve_subtopic(theme: str, role: str, account_id: str) -> str:
    """Return a clean concrete topic sentence. Falls back to the broad theme if the
    LLM fails, so the pipeline never hard-blocks."""
    prompt = (
        f"Broad theme: {theme}\n"
        f"Account persona: {role}\n"
        "Propose the single best concrete subtopic to debate right now."
    )
    try:
        out = await router.generate(
            prompt, system_prompt=_SYSTEM, task=TaskType.SPEED_BATCH, temperature=0.8,
        )
        topic = out.strip().strip('"').strip("'").lstrip("- ").strip()
        # Defensive: if the model ignored instructions and emitted meta-language,
        # collapse it — but keep it as the topic rather than crashing.
        if not topic or len(topic) > 300:
            topic = theme
        _log_io(theme, role, account_id, topic, ok=True)
        return topic
    except Exception as e:
        logger.warning("subtopic resolve failed (%s); using broad theme", e)
        _log_io(theme, role, account_id, theme, ok=False, err=str(e)[:200])
        return theme


async def resolve_tone_plan(characters: list, topic: str, turns: int = 6) -> dict:
    """Return a per-turn emotion plan for EACH character, so tone shifts across the
    debate (not one flat persona dial). Returns {character_key: [emotion_per_turn]}.

    The writer consumes this to apply the existing emotion module (prosody + RVC
    pitch) per line, per character, independently.
    """
    if turns < 1:
        turns = 6
    plan = {}
    for ch in characters:
        prompt = (
            f"Character: {ch.get('name', ch.get('id', 'char'))}\n"
            f"Persona: {ch.get('voice_persona', '')}\n"
            f"Debate topic: {topic}\n"
            f"Output exactly {turns} emotion labels (one word each, comma-separated) "
            f"showing how this character's tone SHOULD SHIFT across the debate — e.g. "
            f"confident, provocative, agitated, mocking, smug, reflective. Vary it; "
            f"do not repeat the same word. Return ONLY the comma list."
        )
        try:
            out = await router.generate(
                prompt,
                system_prompt="You output ONLY a comma-separated list of emotion words. No prose.",
                task=TaskType.SPEED_BATCH, temperature=0.7,
            )
            labels = [w.strip().lower() for w in out.split(",") if w.strip()][:turns]
            # pad/truncate to exactly `turns`
            while len(labels) < turns:
                labels.append(labels[-1] if labels else "neutral")
            plan[ch.get("id", ch.get("name", "char"))] = labels[:turns]
        except Exception as e:
            logger.warning("tone plan failed for %s: %s", ch.get("id"), e)
            plan[ch.get("id", ch.get("name", "char"))] = ["neutral"] * turns
    return plan


def resolve_subtopic_sync(theme: str, role: str, account_id: str) -> str:
    """Sync wrapper around resolve_subtopic (planner runs outside an event loop)."""
    import asyncio
    try:
        return asyncio.get_event_loop().run_until_complete(
            resolve_subtopic(theme, role, account_id))
    except Exception:
        # event loop may already be running in some contexts; fall back to new loop
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(resolve_subtopic(theme, role, account_id))
        finally:
            loop.close()


def resolve_tone_plan_sync(characters: list, topic: str, turns: int = 6) -> dict:
    """Sync wrapper around resolve_tone_plan."""
    import asyncio
    try:
        return asyncio.get_event_loop().run_until_complete(resolve_tone_plan(characters, topic, turns))
    except Exception:
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(resolve_tone_plan(characters, topic, turns))
        finally:
            loop.close()
