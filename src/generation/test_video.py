import asyncio
import logging
from src.core.config import config
from src.generation.tts import generate_speech
from src.generation.video import VideoGenerator

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("content_engine.test_video")

async def test_full_video_generation():
    logger.info("Initializing full Video Generation Pipeline integration test...")
    
    # 1. Setup sample script (DBZ universe style)
    script_text = "Goku vs Vegeta is the absolute greatest rivalry in anime history. From their first clash on Earth to fight alongside each other in space, their battle for supremacy defined Dragon Ball Z. Who is your goat?"
    voice = "en-US-GuyNeural"
    
    # Paths
    audio_path = str(config.OUTPUTS_DIR / "test_tts_dbz.wav")
    video_output_path = str(config.OUTPUTS_DIR / "test_reel_dbz.mp4")
    
    try:
        # 2. Generate TTS narration
        logger.info("\n--- STEP 1: GENERATING TTS AUDIO ---")
        await generate_speech(script_text, voice, audio_path)
        logger.info(f"TTS Audio successfully generated at: {audio_path}")
        
        # 3. Transcribe TTS via Deepgram (word-by-word)
        logger.info("\n--- STEP 2: TRANSCRIBING AUDIO VIA DEEPGRAM ---")
        words = await VideoGenerator.transcribe_audio_deepgram(audio_path)
        logger.info(f"Successfully transcribed {len(words)} words.")
        
        # 4. Fetch background clips (gaming/abstract) from Pexels Video API
        logger.info("\n--- STEP 3: SEARCHING & DOWNLOADING BACKGROUND B-ROLL ---")
        background_clips = await VideoGenerator.fetch_pexels_stock_videos("space aesthetic", max_duration=15.0)
        if not background_clips:
            raise FileNotFoundError("Pexels failed to return B-roll footage.")
        
        selected_bg = background_clips[0]
        logger.info(f"Selected B-roll clip: {selected_bg}")
        
        # 5. Compose the reel using FFmpeg
        logger.info("\n--- STEP 4: COMPOSING VERTICAL REEL WITH FFMPEG ---")
        await VideoGenerator.compose_reel(
            background_video_path=selected_bg,
            audio_path=audio_path,
            words=words,
            output_mp4_path=video_output_path
        )
        
        logger.info(f"\n=== VIDEO GENERATION INTEGRATION TEST SUCCESSFUL! ===")
        logger.info(f"Final MP4 Reel generated successfully at: {video_output_path}")
        
    except Exception as e:
        logger.error(f"Video pipeline test failed: {str(e)}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(test_full_video_generation())
