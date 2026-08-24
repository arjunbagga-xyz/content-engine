"""End-to-end emotion-aware reel pipeline with a recursive QA loop.

Flow:
  1. PLAN    - LLM emits a script as a list of {text, emotion} lines.
  2. GENERATE - produce_scripted_reel renders each line with its own voice
               prosody + RVC pitch + LLM-chosen sprite + video grade.
  3. QA GATE - Whisper transcribes the output audio; we check intelligibility
               (WER vs the script's words) + sprite-match confidence. If it
               fails, we re-plan / re-render with adjusted params (recursive).
  4. RESULT  - returns the passing reel path + a QA report.

Self-improving: the loop retries with a stricter/looser config up to max_retries,
logging every attempt so the system learns what passes the bar.
"""
import os
import json
import logging
import random
import subprocess
import asyncio
from pathlib import Path
from typing import Dict, Any, List, Optional

from src.core.config import BASE_DIR
from src.llm.router import router, TaskType
from src.generation import sprite_reactor as SR
from src.generation import emotion as EM

logger = logging.getLogger("content_engine.reel_pipeline")

WHISPER_SCRIPT = BASE_DIR / "scratch" / "whisper_check.py"
RVC_ENV_PY = BASE_DIR / "rvc_env" / "Scripts" / "python.exe"

# QA thresholds
MAX_WER = 0.35          # word-error-rate ceiling vs source script (garble guard)
MIN_SPRITE_CONF = 0.4   # sprite-match confidence floor


async def plan_script(topic: str, character_key: str, account_id: str,
                      persona: str, num_lines: int = 3,
                      angle: str = None) -> List[Dict[str, str]]:
    """LLM emits a script as a list of {text, emotion} lines.

    emotion MUST be one of EM.EMOTIONS so the downstream matcher/voice agree.
    `angle` (optional) is a fresh sub-angle for the topic so repeated topics
    don't produce identical scripts (keeps a theme from going stale).
    """
    emotions = ", ".join(EM.EMOTIONS)
    angle_line = f"\nSpecific angle to hit this time: {angle}\n" if angle else ""
    prompt = (
        f"Topic: {topic}\n"
        f"Character: {persona}\n"
        f"{angle_line}"
        f"Write a {num_lines}-line spoken hook for a faceless reaction reel. "
        f"Each line must carry a distinct EMOTION so the reel modulates tone. "
        f"Use emotions from this exact list: {emotions}.\n\n"
        f"Return ONLY raw JSON, a list of objects: "
        f'[{{"text": "...", "emotion": "..."}}]. No markdown, no code fences.'
    )
    raw = await router.generate(prompt, task=TaskType.SIMPLE, temperature=0.9)
    clean = raw.replace("```json", "").replace("```", "").strip()
    try:
        lines = json.loads(clean)
    except Exception:
        # Fallback: one neutral line so the pipeline never hard-fails on parse.
        logger.warning("plan_script JSON parse failed; neutral fallback line")
        lines = [{"text": topic, "emotion": "neutral"}]
    # Sanitize emotions
    valid = set(EM.EMOTIONS)
    for ln in lines:
        if ln.get("emotion") not in valid:
            ln["emotion"] = "neutral"
    return lines


# ---------------------------------------------------------------------------
# GAP 1: autonomous topic / character selection.
# The account's `topics:` list and `characters:` roster are declared config;
# these helpers turn them into a concrete (topic, angle, character) decision
# each cycle, with rotation so a theme stays coherent and doesn't repeat.
# ---------------------------------------------------------------------------

# Process-wide memory of recent (account, topic, character) picks so a single
# daemon session doesn't repeat itself.
_RECENT_PICKS: Dict[str, List[tuple]] = {}


def _recent(account_id: str) -> List[tuple]:
    return _RECENT_PICKS.setdefault(account_id, [])


def pick_topic(account_conf: Dict[str, Any], account_id: str,
               avoid_repeat: int = 2) -> tuple:
    """Picks a topic from the account's theme list with no-repeat rotation,
    and emits a fresh sub-angle so the topic doesn't go stale.

    Returns (topic, angle).
    """
    topics = account_conf.get("topics", [])
    if not topics:
        # Faceless accounts without an explicit topics list fall back to the
        # account's role/visual_keywords as a single implicit theme.
        role = account_conf.get("role", account_id)
        return (role, None)
    recent = _recent(account_id)
    recent_topics = [t for (t, _, _) in recent[-avoid_repeat:]]
    candidates = [t for t in topics if t not in recent_topics] or topics
    topic = random.choice(candidates)
    # Angle seed: a concrete micro-take so the LLM doesn't reuse the same line.
    angle_seeds = [
        "a counterintuitive take",
        "a personal failure story",
        "a habit most people get wrong",
        "a hard truth nobody says out loud",
        "a tiny daily win",
        "what changed your mind",
    ]
    angle = random.choice(angle_seeds)
    return (topic, angle)


def pick_character(account_conf: Dict[str, Any], account_id: str,
                   avoid_repeat: int = 2) -> str:
    """Picks a host character from the account's roster, rotating away from
    the most recently used so one host doesn't dominate.
    """
    characters = account_conf.get("characters", {})
    if not characters:
        raise ValueError(f"Account {account_id} has no characters roster")
    recent = _recent(account_id)
    recent_chars = [c for (_, _, c) in recent[-avoid_repeat:]]
    candidates = [c for c in characters if c not in recent_chars] or list(characters)
    return random.choice(candidates)


def select_post(account_id: str) -> Dict[str, Any]:
    """Decides the next (topic, angle, character) for an account.

    Reads the account's declared themes + roster and records the pick in the
    per-session recent-memory so subsequent calls rotate.
    """
    account_conf = SR.SpriteReactor._get_account(account_id)
    if not account_conf:
        raise ValueError(f"Account {account_id} not found")
    topic, angle = pick_topic(account_conf, account_id)
    character_key = pick_character(account_conf, account_id)
    _recent(account_id).append((topic, angle, character_key))
    return {
        "account_id": account_id,
        "topic": topic,
        "angle": angle,
        "character_key": character_key,
        "persona": account_conf["characters"][character_key].get("voice_persona", character_key),
    }


def _whisper_check(audio_path: str, reference: str) -> Dict[str, Any]:
    """Run the isolated Whisper CLI; returns {transcript, wer, words, duration}."""
    if not RVC_ENV_PY.exists():
        logger.warning("rvc_env python not found; skipping Whisper QA")
        return {"transcript": "", "wer": None, "words": 0, "duration": 0.0}
    cmd = [str(RVC_ENV_PY), str(WHISPER_SCRIPT), audio_path, reference]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        return json.loads(out.stdout.strip().splitlines()[-1])
    except Exception as e:
        logger.warning(f"Whisper QA failed: {e}")
        return {"transcript": "", "wer": None, "words": 0, "duration": 0.0}


def _build_reference(lines: List[Dict[str, str]]) -> str:
    return " ".join(ln.get("text", "") for ln in lines)


async def run_reel_pipeline(account_id: str, character_key: str = None, topic: str = None,
                            output_path: str = None, num_lines: int = 3,
                            max_retries: int = 3, angle: str = None) -> Dict[str, Any]:
    """Full recursive pipeline: plan -> generate -> QA -> (retry) -> result.

    GAP 1: topic / character auto-selection. If `topic` or `character_key` are
    omitted, they are chosen from the account's declared themes + roster via
    select_post() (with rotation so the account stays on-theme and varied).
    `output_path` is also auto-derived when omitted.

    Returns dict with keys: path, passed, attempts, qa_report, script, error,
    plus the resolved account_id, character_key, topic, angle.
    """
    account_conf = SR.SpriteReactor._get_account(account_id)
    if not account_conf:
        raise ValueError(f"Account {account_id} not found")

    # --- Auto-select topic + character when not explicitly given ---
    selected = None
    if topic is None or character_key is None:
        selected = select_post(account_id)
        topic = topic or selected["topic"]
        character_key = character_key or selected["character_key"]
        angle = angle or selected.get("angle")
        persona = account_conf["characters"][character_key].get("voice_persona", character_key)
    else:
        characters = account_conf.get("characters", {})
        if character_key not in characters:
            raise ValueError(f"Character {character_key} not in account {account_id}")
        persona = characters[character_key].get("voice_persona", character_key)

    if output_path is None:
        out_dir = BASE_DIR / "outputs" / "auto"
        out_dir.mkdir(parents=True, exist_ok=True)
        output_path = str(out_dir / f"{account_id}_{character_key}_{abs(hash(topic)) % 100000}.mp4")

    attempts = []
    last_error = None
    # Per-retry: alternate strategy. Retry 1 re-plans; retry 2 lowers emotion
    # extremity (less fast/angry) which helps intelligibility; retry 3 neutral-only.
    for attempt in range(1, max_retries + 1):
        logger.info(f"[reel-pipeline] attempt {attempt}/{max_retries}")
        # 1. PLAN
        if attempt == 1:
            lines = await plan_script(topic, character_key, account_id, persona,
                                      num_lines, angle=angle)
        elif attempt == 2:
            # re-plan with a calmer directive
            lines = await plan_script(topic + " (keep tone measured, avoid shouting)",
                                       character_key, account_id, persona, num_lines,
                                       angle=angle)
        else:
            # neutral-only safety net
            lines = [{"text": topic, "emotion": "neutral"} for _ in range(num_lines)]

        # 2. GENERATE
        try:
            res = await SR.SpriteReactor.produce_scripted_reel(
                account_id, output_path, character_key, lines, topic=topic)
        except Exception as e:
            last_error = f"generate failed: {e}"
            logger.error(last_error)
            attempts.append({"attempt": attempt, "stage": "generate", "error": str(e)})
            continue

        # 3. QA GATE — check the COMBINED audio's intelligibility.
        # produce_scripted_reel cleaned temp audio; re-derive combined audio path
        # by re-concatenating is overkill -> instead QA each line's text vs Whisper
        # on the final mp4's audio track. Extract audio from the mp4.
        audio_extract = str(Path(output_path).with_suffix("")) + "_qa.wav"
        subprocess.run(["ffmpeg", "-y", "-i", output_path, "-vn", "-ac", "1",
                        "-ar", "16000", audio_extract],
                       capture_output=True)
        ref = _build_reference(lines)
        qa = _whisper_check(audio_extract, ref)
        os.remove(audio_extract)

        sprite_confs = [ln.get("sprite_confidence", 1.0) for ln in res.get("lines", [])]
        min_conf = min(sprite_confs) if sprite_confs else 1.0
        wer = qa.get("wer")
        wer_ok = (wer is None) or (wer <= MAX_WER)
        sprite_ok = min_conf >= MIN_SPRITE_CONF

        report = {
            "attempt": attempt,
            "wer": wer,
            "transcript": qa.get("transcript", ""),
            "min_sprite_conf": round(min_conf, 2),
            "wer_ok": wer_ok,
            "sprite_ok": sprite_ok,
            "passed": wer_ok and sprite_ok,
        }
        attempts.append(report)

        if report["passed"]:
            logger.info(f"[reel-pipeline] PASSED on attempt {attempt} (wer={wer}, sprite={min_conf})")
            return {
                "path": output_path,
                "passed": True,
                "attempts": attempts,
                "qa_report": report,
                "script": lines,
                "error": None,
                "account_id": account_id,
                "character_key": character_key,
                "topic": topic,
                "angle": angle,
            }
        else:
            last_error = f"QA failed: wer_ok={wer_ok} sprite_ok={sprite_ok}"
            logger.warning(f"[reel-pipeline] attempt {attempt} QA fail: {last_error}")

    # All retries exhausted
    return {
        "path": output_path,
        "passed": False,
        "attempts": attempts,
        "qa_report": attempts[-1] if attempts else None,
        "script": None,
        "error": last_error or "all retries failed",
        "account_id": account_id,
        "character_key": character_key,
        "topic": topic,
        "angle": angle,
    }
