import asyncio
import logging
from src.llm.router import router, TaskType

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("content_engine.test_router")

async def main():
    logger.info("Starting LLM Router integration test...")
    
    # 1. Test Creative Writing (should use Groq or Gemini)
    prompt = "Write a quick sarcastic tweet about coding at 3 AM. Keep it under 200 characters, no hashtags."
    system_prompt = "You are Maya, a chaotic indie game dev girl. You are caffeinated and sarcastic."
    
    logger.info("Testing Creative Writing task...")
    try:
        response = await router.generate(
            prompt=prompt,
            system_prompt=system_prompt,
            task=TaskType.CREATIVE_WRITING,
            temperature=0.8
        )
        logger.info("\n--- CREATIVE WRITING RESPONSE ---")
        logger.info(response)
        logger.info("---------------------------------\n")
    except Exception as e:
        logger.error(f"Creative Writing test failed: {str(e)}")

    # 2. Test Planning (should prefer Gemini)
    prompt = "Brainstorm 3 indie game concept titles based on retro hardware limitations. Return just the list."
    
    logger.info("Testing Planning task...")
    try:
        response = await router.generate(
            prompt=prompt,
            task=TaskType.PLANNING,
            temperature=0.7
        )
        logger.info("\n--- PLANNING RESPONSE ---")
        logger.info(response)
        logger.info("-------------------------\n")
    except Exception as e:
        logger.error(f"Planning test failed: {str(e)}")

if __name__ == "__main__":
    asyncio.run(main())
