"""Chat Roast pipeline — fake WhatsApp argument between two characters.

Uses html_renderer (Playwright + HTML/CSS) for the UI and RVC voices.
"""
import os, sys, json, logging, asyncio
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
logger = logging.getLogger("content_engine.pipeline.chat_roast")

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
    # fallback: sprites_clean
    clean = PROJECT_ROOT / "data" / "sprites_clean"
    if clean.exists():
        for f in clean.glob("*.png"):
            if character_name.lower() in f.name.lower():
                return str(f)
    return ""


async def generate_script(c1, c2, topic, turns=8):
    """LLM writes a fake WhatsApp argument between two characters."""
    from src.generation.pipeline_impl.llm_helper import generate_text
    prompt = (
        f"Write a fake WhatsApp chat argument between {c1['name']} and {c2['name']}.\n"
        f"TOPIC: {topic}\n"
        f"{c1['name']} persona: {c1['persona']}\n"
        f"{c2['name']} persona: {c2['persona']}\n\n"
        "Rules:\n"
        "- Short punchy bubbles (5-15 words each)\n"
        "- Texting language: bro, ngl, fr, tbh, lmao, bruh, wtf, idc\n"
        "- ALL CAPS for emphasis sometimes\n"
        "- No formal punctuation, lowercase is fine\n"
        "- Roasting/arguing energy\n"
        "- Alternate speakers (no double-texting)\n"
        "- End with a mic drop\n\n"
        "Output JSON array ONLY:\n"
        '[{"speaker": "' + c1['name'] + '", "text": "message"}, ...]'
    )
    text = await generate_text(prompt, 0.8)
    try:
        s, e = text.find("["), text.rfind("]") + 1
        if s >= 0 and e > 0:
            return json.loads(text[s:e])
    except Exception:
        pass
    return [
        {"speaker": c1["name"], "text": f"bro let's settle this about {topic}"},
        {"speaker": c2["name"], "text": "im listening but you wont like it"},
    ]


async def run_pipeline(account_id, output_path, topic=None, num_turns=None, tone_plan=None):
    """Full pipeline: script → RVC voices → HTML render → video."""
    from src.generation import sprite_reactor as SR
    from src.generation.pipeline_impl.html_renderer import render_whatsapp_chat
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

    # Topic: use the planner's resolved subtopic if none passed
    if not topic:
        topic = "fitness"
    if not str(topic).strip():
        topic = "fitness"

    logger.info("Chat Roast: %s vs %s on '%s'", c1["name"], c2["name"], topic)

    script = await generate_script(c1, c2, topic, turns=num_turns or 8)
    if not script:
        return None

    # Generate RVC voices per message
    audio_files = []
    for i, m in enumerate(script):
        sp = m["speaker"]
        voice = roster.get(sp, {}).get("tts_voice", "en-US-GuyNeural")
        fp = str(OUTPUT_DIR / f"_chat_roast_{i}.wav")
        await generate_voice(m["text"], sp, fp, voice)
        audio_files.append(fp)

    # Avatars from sprites
    me_avatar = _avatar_for(c1["name"])
    contact_avatar = _avatar_for(c2["name"])

    out = await render_whatsapp_chat(
        script, audio_files, output_path,
        contact_name=c2["name"], contact_avatar=contact_avatar,
        me_avatar=me_avatar, seed=42,
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
        str(OUTPUT_DIR / "chat_roast_test.mp4"),
        topic="who has better workout form",
    )
