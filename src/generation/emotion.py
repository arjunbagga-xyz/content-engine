"""Emotion system: drive voice prosody, sprite selection, and video tonality.

One emotion tag flows through the whole pipeline:
  script line -> emotion -> (SSML/style narrator) -> RVC voice
                  emotion -> LLM sprite match -> sprite PNG
                  emotion -> video grade (color/shake)

Emotion is a controlled vocabulary so the LLM planner, the voice mapper, and the
sprite matcher all speak the same language. Sprite matching is SEMANTIC (LLM), not
string equality: "angry" should match a sprite tagged "raging" or "fighting", not
just one literally named "angry".
"""
import json
import logging
import random
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

from src.core.config import BASE_DIR
from src.llm.router import router, TaskType

logger = logging.getLogger("content_engine.emotion")

# ---------------------------------------------------------------------------
# Emotion vocabulary (controlled set shared by planner / voice / sprite / video)
# ---------------------------------------------------------------------------
EMOTIONS = [
    "neutral", "excited", "angry", "happy", "sad",
    "smug", "sarcastic", "shocked", "calm", "threatening", "determined",
]

# ---------------------------------------------------------------------------
# Voice prosody per emotion.
#  - style:    Edge-TTS "style" attribute (only some voices support each; we fall
#              back to SSML <prosody> when unsupported or unknown).
#  - pitch:    SSML pitch ("+20%", "-10%", "medium", "high", "low")
#  - rate:     SSML rate ("fast", "slow", "medium", "x-fast")
#  - volume:   SSML volume ("loud", "soft", "medium")
# SSML always works regardless of voice style support, so it is the reliable layer.
# ---------------------------------------------------------------------------
VOICE_PROSODY: Dict[str, Dict[str, str]] = {
    "neutral":    {"style": "neutral",    "pitch": "+0%",    "rate": "medium", "volume": "medium"},
    "excited":    {"style": "excited",    "pitch": "+18%",   "rate": "+8%",    "volume": "loud"},
    "angry":      {"style": "angry",      "pitch": "+10%",   "rate": "+8%",    "volume": "loud"},
    "happy":      {"style": "cheerful",   "pitch": "+15%",   "rate": "medium", "volume": "medium"},
    "sad":        {"style": "sad",        "pitch": "-12%",   "rate": "-10%",   "volume": "soft"},
    "smug":       {"style": "smug",       "pitch": "+5%",    "rate": "medium", "volume": "medium"},
    "sarcastic":  {"style": "sarcastic",  "pitch": "+8%",    "rate": "medium", "volume": "medium"},
    "shocked":    {"style": "shouted",    "pitch": "+22%",   "rate": "+12%",   "volume": "loud"},
    "calm":       {"style": "calm",       "pitch": "-3%",    "rate": "-8%",    "volume": "medium"},
    "threatening":{"style": "angry",      "pitch": "-6%",    "rate": "-5%",    "volume": "medium"},
    "determined": {"style": "serious",    "pitch": "+5%",    "rate": "+3%",    "volume": "medium"},
}

# Per-character RVC pitch offset (semitones) to match each voice's register.
# 0 = no shift. Tuned so e.g. Goku (higher, energetic) sits a bit up, Vegeta lower.
PER_CHAR_RVC_PITCH: Dict[str, int] = {
    "goku": 2,
    "vegeta": -1,
    "gohan": 0,
    "peter": -2,
    "stewie": 4,
    "meg": 1,
    # New real/cartoon-figure voices (dropped in from models/new)
    "tate": 0,
    "trump": 1,
    "elon": 2,
    "zuck": 3,
    "hulk": -3,
    "peppa": 6,
}

# ---------------------------------------------------------------------------
# Video tonality per emotion (passed to compose_sprite_reel as a grade key).
# ---------------------------------------------------------------------------
VIDEO_GRADE: Dict[str, str] = {
    "neutral": "none",
    "excited": "pulse",
    "angry": "red_shake",
    "happy": "warm_glow",
    "sad": "desaturate",
    "smug": "gold_tint",
    "sarcastic": "cyan_tint",
    "shocked": "flash",
    "calm": "soft_vignette",
    "threatening": "dark_red",
    "determined": "amber_boost",
}


def get_prosody(emotion: str) -> Dict[str, str]:
    return VOICE_PROSODY.get(emotion, VOICE_PROSODY["neutral"])


def get_rvc_pitch(character: str) -> int:
    return PER_CHAR_RVC_PITCH.get(character, 0)


def get_video_grade(emotion: str) -> str:
    return VIDEO_GRADE.get(emotion, "none")


def edge_tts_args(emotion: str) -> Dict[str, str]:
    """Map an emotion to edge_tts.Communicate kwargs.

    edge_tts validates: rate/pitch as '[+-]N%' or '[+-]NHz', volume as '[+-]N%'.
    - rate/pitch in prosody are already percentages ('+8%', '-8%') -> pass through.
    - pitch percentage ('+10%') must become Hz ('+10Hz') for edge_tts.
    - volume uses words ('loud'/'soft'/'medium') -> map to percentages.
    """
    prosody = get_prosody(emotion)
    vol_map = {"loud": "+20%", "soft": "-20%", "medium": "+0%"}
    rate = prosody["rate"]
    if rate not in ("+0%",) and not rate.startswith(("+", "-")):
        # legacy word forms
        rate = {"fast": "+20%", "x-fast": "+35%", "slow": "-20%", "medium": "+0%"}.get(rate, "+0%")
    pitch = prosody["pitch"]
    if pitch.endswith("%"):
        pitch = pitch[:-1] + "Hz"
    return {
        "rate": rate if rate.startswith(("+", "-")) else "+0%",
        "volume": vol_map.get(prosody["volume"], "+0%"),
        "pitch": pitch,
    }


# ---------------------------------------------------------------------------
# LLM semantic sprite matcher
# ---------------------------------------------------------------------------
def _candidate_list(character: str) -> List[str]:
    folder = BASE_DIR / "data" / "characters" / character / "sprites"
    if not folder.exists():
        return []
    return [f.name for f in folder.glob("*.png")]


_SPRITE_MATCH_PROMPT = (
    "You are a visual casting director for animated character reaction videos.\n"
    "Given a character, the EMOTIONAL/PHYSICAL MOMENT a line is delivered in, and a list "
    "of available sprite image filenames, pick the sprite that BEST matches that moment.\n"
    "Match by MEANING, not exact words: an 'angry' line can be served by a sprite tagged "
    "'raging', 'fighting', 'powering_up', 'yelling', etc. A 'calm' line wants a resting or "
    "smiling pose, not a combat one.\n\n"
    "Respond with STRICT JSON only:\n"
    "{\"sprite\": \"<exact filename from the list>\", \"confidence\": <0.0-1.0>, "
    "\"reason\": \"<one short phrase>\"}\n"
    "If NONE fit well, pick the closest neutral/available sprite and set confidence low (<0.5)."
)


async def llm_pick_sprite(character: str, emotion: str,
                          context: str = "") -> Tuple[str, float, str]:
    """Semantic sprite pick via LLM. Returns (filename, confidence, reason).

    Falls back to a random sprite for the character if no candidates or LLM fails.
    """
    candidates = _candidate_list(character)
    if not candidates:
        return ("", 0.0, "no sprites available")

    prompt = (
        f"Character: {character}\n"
        f"Moment / emotion: {emotion}\n"
        f"Context of the line: {context}\n\n"
        f"Available sprites:\n" + "\n".join(f"- {c}" for c in candidates) + "\n"
    )
    try:
        raw = await router.generate(
            prompt, system_prompt=_SPRITE_MATCH_PROMPT,
            task=TaskType.SIMPLE, temperature=0.3,
        )
        # Extract the first JSON object from the response.
        start = raw.find("{")
        end = raw.rfind("}") + 1
        if start == -1 or end <= start:
            raise ValueError("no json in response")
        data = json.loads(raw[start:end])
        sprite = data.get("sprite", "")
        conf = float(data.get("confidence", 0.0))
        reason = data.get("reason", "")
        if sprite not in candidates:
            # LLM hallucinated a name; fall back to random.
            sprite = random.choice(candidates)
            conf = 0.3
            reason = "llm name mismatch -> random fallback"
        return (sprite, conf, reason)
    except Exception as e:
        logger.warning(f"llm_pick_sprite failed ({e}); random fallback")
        return (random.choice(candidates), 0.2, "error -> random fallback")
