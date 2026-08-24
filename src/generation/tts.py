import logging
import edge_tts
from src.core.config import config

logger = logging.getLogger("content_engine.tts")

async def generate_speech(text: str, voice: str, output_path: str) -> str:
    """Asynchronously generates neural speech audio using Microsoft Edge TTS.
    
    Args:
        text: The text script to read aloud.
        voice: The standard edge-tts voice code (e.g. 'en-US-AnaNeural').
        output_path: Path where the generated MP3/audio file should be saved.
        
    Returns:
        The path to the generated audio file.
    """
    logger.info(f"Generating TTS audio using voice {voice}...")
    try:
        communicate = edge_tts.Communicate(text, voice)
        await communicate.save(output_path)
        logger.info(f"TTS audio successfully generated and saved to {output_path}")
        return output_path
    except Exception as e:
        logger.error(f"Failed to generate TTS audio: {str(e)}")
        raise e
