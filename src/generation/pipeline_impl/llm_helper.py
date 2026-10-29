"""LLM helper - uses the project's router for text generation."""
import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

logger = logging.getLogger("content_engine.llm_helper")


async def generate_text(prompt: str, temperature: float = 0.8, max_tokens: int = 800) -> str:
    """Generate text via the project's LLM router."""
    from src.llm.router import router, TaskType
    
    try:
        result = await router.generate(
            prompt, 
            task=TaskType.CREATIVE_WRITING,
            temperature=temperature,
        )
        return result
    except Exception as e:
        logger.error("LLM generation failed: %s", e)
        return ""


def generate_text_sync(prompt: str, temperature: float = 0.8, max_tokens: int = 800) -> str:
    """Synchronous wrapper."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(generate_text(prompt, temperature, max_tokens))
    finally:
        loop.close()
