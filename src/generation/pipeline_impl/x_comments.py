"""X Comments pipeline — fake X/Twitter comment thread argument.

Uses html_renderer (Playwright + HTML/CSS) for the UI and RVC voices.
"""
import os, sys, json, logging, asyncio
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
logger = logging.getLogger("content_engine.pipeline.x_comments")

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


async def generate_thread(c1, c2, topic, turns=8):
    """LLM writes a fake X comment thread argument."""
    from src.generation.pipeline_impl.llm_helper import generate_text
    prompt = (
        f"Write a fake X (Twitter) comment thread argument about {topic}.\n"
        f"Participants: @{c1['name']} and @{c2['name']}\n"
        f"{c1['name']} persona: {c1['persona']}\n"
        f"{c2['name']} persona: {c2['persona']}\n\n"
        "Rules:\n"
        "- Short, punchy comments (5-20 words)\n"
        "- Use X language: ratio, L, W, based, cringe, no cap, fr, ngl\n"
        "- Each comment is a reply to the previous one\n"
        "- Make it funny and in-character\n"
        "- End with a mic drop\n\n"
        "Output JSON array:\n"
        '[{"user": "' + c1['name'] + '", "text": "comment"}, ...]'
    )
    text = await generate_text(prompt, 0.8)
    try:
        s, e = text.find("["), text.rfind("]") + 1
        if s >= 0 and e > 0:
            return json.loads(text[s:e])
    except Exception:
        pass
    return [
        {"user": c1["name"], "text": f"@{c2['name']} you're completely wrong about {topic}"},
        {"user": c2["name"], "text": "the audacity of this take"},
    ]


async def run_pipeline(account_id, output_path, topic=None, num_turns=None, tone_plan=None):
    """Full pipeline: thread → RVC voices → HTML render → video."""
    from src.generation import sprite_reactor as SR
    from src.generation.pipeline_impl.html_renderer import render_x_feed
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

    logger.info("X Comments: %s vs %s on '%s'", c1["name"], c2["name"], topic)

    thread = await generate_thread(c1, c2, topic, turns=num_turns or 8)
    if not thread:
        return None

    # Generate RVC voices per comment
    audio_files = []
    for i, c in enumerate(thread):
        sp = c["user"]
        voice = roster.get(sp, {}).get("tts_voice", "en-US-GuyNeural")
        fp = str(OUTPUT_DIR / f"_x_comments_{i}.wav")
        await generate_voice(c["text"], sp, fp, voice)
        audio_files.append(fp)

    # Avatars from sprites
    avatars = {
        c1["name"]: _avatar_for(c1["name"]),
        c2["name"]: _avatar_for(c2["name"]),
    }

    out = await render_x_feed(
        thread, audio_files, output_path,
        avatars=avatars, seed=42,
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
        str(OUTPUT_DIR / "x_comments_test.mp4"),
        topic="who would win in a fight",
    )
