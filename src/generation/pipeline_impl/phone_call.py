"""Phone Call pipeline — fake WhatsApp voice call between two characters.

Uses html_renderer (Playwright + HTML/CSS) for the UI and RVC voices.
"""
import os, sys, json, logging, asyncio
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
logger = logging.getLogger("content_engine.pipeline.phone_call")

PROJECT_ROOT = Path(__file__).resolve().parents[3]
OUTPUT_DIR = PROJECT_ROOT / "outputs"

AVATAR_DIR = PROJECT_ROOT / "data" / "characters"


def _avatar_for(character_name: str) -> str:
    """Find the character's sprite avatar (any PNG in their sprites folder)."""
    folder = AVATAR_DIR / character_name.lower() / "sprites"
    if folder.exists():
        pngs = sorted(folder.glob("*.png"))
        if pngs:
            return str(pngs[0])
    clean = PROJECT_ROOT / "data" / "sprites_clean"
    if clean.exists():
        for f in clean.glob("*.png"):
            if character_name.lower() in f.name.lower():
                return str(f)
    return ""


async def generate_dialogue(c1, c2, topic, turns=8):
    """LLM writes a fake phone call transcript."""
    from src.generation.pipeline_impl.llm_helper import generate_text
    prompt = (
        f"Write a fake phone call transcript between {c1['name']} and {c2['name']}.\n"
        f"TOPIC: {topic}\n"
        f"{c1['name']} persona: {c1['persona']}\n"
        f"{c2['name']} persona: {c2['persona']}\n\n"
        "Rules:\n"
        "- Natural speech: um, like, uh, pauses\n"
        "- Conversational, not formal\n"
        "- Build tension through the call\n"
        "- End dramatic or funny\n\n"
        "Output JSON array:\n"
        '[{"speaker": "' + c1['name'] + '", "text": "dialogue"}, ...]'
    )
    text = await generate_text(prompt, 0.8)
    try:
        s, e = text.find("["), text.rfind("]") + 1
        if s >= 0 and e > 0:
            return json.loads(text[s:e])
    except Exception:
        pass
    return [
        {"speaker": c1["name"], "text": f"hey can we talk about {topic}"},
        {"speaker": c2["name"], "text": "yeah go ahead"},
        {"speaker": c1["name"], "text": "i just think youre wrong"},
        {"speaker": c2["name"], "text": "no YOU'RE wrong"},
    ]


async def run_pipeline(account_id, output_path, topic=None, num_turns=None, tone_plan=None):
    """Full pipeline: dialogue → RVC voices → HTML render → video."""
    from src.generation import sprite_reactor as SR
    from src.generation.pipeline_impl.html_renderer import render_whatsapp_call
    from src.generation.voice_provider import generate_voice

    acct = SR.SpriteReactor._get_account(account_id) or {}
    roster = {}
    for ck, cd in (acct.get("characters") or {}).items():
        roster[ck] = {
            "persona": cd.get("voice_persona", ""),
            "sprite_tags": cd.get("sprite_tags", [ck]),
            "tts_voice": cd.get("tts_voice", "en-US-GuyNeural"),
        }
    leads = list(acct.get("speakers", [])) or list(roster.keys())[:2]
    chars = [roster[l] for l in leads if l in roster]
    if len(chars) < 2:
        chars = [
            {"name": "Goku", "persona": "cheerful naive", "tts_voice": "en-US-GuyNeural"},
            {"name": "Vegeta", "persona": "proud angry", "tts_voice": "en-US-SteffanNeural"},
        ]

    c1, c2 = chars[0], chars[1]
    c1["name"] = leads[0] if leads else "Goku"
    c2["name"] = leads[1] if len(leads) > 1 else "Vegeta"

    if not topic:
        topic = "fitness"
    if not str(topic).strip():
        topic = "fitness"

    logger.info("Phone Call: %s vs %s on '%s'", c1["name"], c2["name"], topic)

    dialogue = await generate_dialogue(c1, c2, topic, turns=num_turns or 8)
    if not dialogue:
        return None

    # Generate RVC voices per line
    audio_files = []
    for i, m in enumerate(dialogue):
        sp = m["speaker"]
        voice = roster.get(sp, {}).get("tts_voice", "en-US-GuyNeural")
        fp = str(OUTPUT_DIR / f"_phone_call_{i}.wav")
        await generate_voice(m["text"], sp, fp, voice)
        audio_files.append(fp)

    # Avatar from sprites
    caller_avatar = _avatar_for(c1["name"])

    out = await render_whatsapp_call(
        dialogue, audio_files, output_path,
        caller_name=c1["name"], caller_avatar=caller_avatar, seed=42,
    )

    # Cleanup temp audio
    for af in audio_files:
        try:
            os.remove(af)
        except OSError:
            pass

    return out


def run_pipeline_sync(*args, **kwargs):
    return asyncio.run(run_pipeline(*args, **kwargs))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run_pipeline_sync(
        "fitness_ball_z",
        str(OUTPUT_DIR / "phone_call_test.mp4"),
        topic="who would win",
    )
