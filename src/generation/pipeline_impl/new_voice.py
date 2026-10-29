"""Voice generation for new pipelines - uses RVC via voice_provider."""
import os, sys, logging
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
logger = logging.getLogger("content_engine.new_pipelines.voice")

# Character voice config from fitness_ball_z
VOICES = {
    "goku": {"narrator": "en-US-GuyNeural", "color": "#007AFF"},
    "vegeta": {"narrator": "en-US-SteffanNeural", "color": "#34C759"},
    "trump": {"narrator": "en-US-SteffanNeural", "color": "#1d9bf0"},
    "elon": {"narrator": "en-US-GuyNeural", "color": "#FF6600"},
    "zuck": {"narrator": "en-US-AndrewNeural", "color": "#1877F2"},
    "peter": {"narrator": "en-US-ChristopherNeural", "color": "#8B5CF6"},
    "hulk": {"narrator": "en-US-BrianNeural", "color": "#10B981"},
    "peppa": {"narrator": "en-US-AriaNeural", "color": "#EC4899"},
    "tate": {"narrator": "en-US-GuyNeural", "color": "#6366F1"},
}


def gen_voice(text, character, out_path):
    """Generate RVC voice for a character."""
    import asyncio
    from src.generation.voice_provider import generate_voice
    
    char_key = character.lower().replace(" ", "_")
    
    async def _gen():
        result = await generate_voice(text, char_key, out_path)
        return result
    
    try:
        return asyncio.run(_gen())
    except Exception as e:
        logger.warning(f"RVC failed for {character}: {e}, falling back to edge-tts")
        # Fallback: just do edge-tts
        import asyncio
        import edge_tts
        voice = VOICES.get(char_key, {}).get("narrator", "en-US-GuyNeural")
        
        async def _fallback():
            await edge_tts.Communicate(text, voice).save(out_path)
        
        asyncio.run(_fallback())
        return {"path": out_path, "method": "edge_tts"}
