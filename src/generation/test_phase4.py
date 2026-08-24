import asyncio
import logging
import json
import os
from pathlib import Path
from src.core.config import config
from src.generation.consistency import ConsistencyPromptBuilder, LoRAInferenceRouter
from src.generation.media_library import MediaLibrary, MediaLibraryCompositor, ManifestBuilder
from src.generation.image import ImageGenerator
from src.generation.video import VideoGenerator
from src.generation.tts import generate_speech

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("content_engine.test_phase4")

async def run_phase4_tests():
    logger.info("=== STARTING PHASE 4 INTEGRATION & STABILITY TESTS ===")

    # -------------------------------------------------------------
    # TEST 1: ConsistencyPromptBuilder & LoRA Router
    # -------------------------------------------------------------
    logger.info("\n--- TEST 1: LoRA Consistency Prompt Builder & Inference fallbacks ---")
    char_yaml = config.load_characters().get("char_1") # Maya
    
    base_prompt = "sitting at a coffee shop workbench, soldering a circuit board, cozy background"
    prompt_data = ConsistencyPromptBuilder.build_prompt(char_yaml, base_prompt)
    
    logger.info(f"Base Prompt: '{base_prompt}'")
    logger.info(f"Expanded LoRA Prompt: '{prompt_data['prompt']}'")
    logger.info(f"Negative Prompt: '{prompt_data['negative_prompt']}'")
    
    assert "ohwx_maya" in prompt_data["prompt"], "Trigger word not injected!"
    assert "messy shoulder-length black hair" in prompt_data["prompt"], "Visual anchor not expanded!"
    
    # Run a test generation of the character portrait (using Pollinations fallback)
    output_portrait = config.OUTPUTS_DIR / "test_maya_consistent.png"
    logger.info(f"Generating Maya portrait via LoRA inference router (Pollinations fallback)...")
    await ImageGenerator.generate_character_portrait(char_yaml, base_prompt, str(output_portrait))
    assert output_portrait.exists(), "Portrait image was not generated!"
    logger.info(f"Verified! Consistent portrait created at: {output_portrait}")

    # -------------------------------------------------------------
    # TEST 2: MediaLibrary Auto-Scan and Manifest Building
    # -------------------------------------------------------------
    logger.info("\n--- TEST 2: Media Library Auto-Scanning & Manifest Building ---")
    
    # Let's mock a media library directory for dbz_verse to verify the auto-scan
    dbz_lib_dir = config.MEDIA_LIBRARY_DIR / "dbz_verse"
    dbz_manifest_file = dbz_lib_dir / "manifest.json"
    
    # Create target folders
    (dbz_lib_dir / "images").mkdir(parents=True, exist_ok=True)
    (dbz_lib_dir / "reactions").mkdir(parents=True, exist_ok=True)
    (dbz_lib_dir / "clips").mkdir(parents=True, exist_ok=True)
    (dbz_lib_dir / "audio").mkdir(parents=True, exist_ok=True)
    
    # Create a couple of mock files to scan
    mock_img_path = dbz_lib_dir / "images" / "vegeta_proud_look.png"
    mock_clip_path = dbz_lib_dir / "clips" / "goku_vs_freeza_clash.mp4"
    
    # Create empty mock files
    if not mock_img_path.exists():
        with open(mock_img_path, "w") as f:
            f.write("mock_image_data")
    if not mock_clip_path.exists():
        with open(mock_clip_path, "w") as f:
            f.write("mock_video_data")

    # Run scanner
    logger.info("Executing manifest auto-scan...")
    ManifestBuilder.auto_scan_and_build(dbz_lib_dir, dbz_manifest_file)
    
    assert dbz_manifest_file.exists(), "Manifest file not created!"
    
    # Load manifest and verify assets were indexed
    with open(dbz_manifest_file, "r") as f:
        manifest_data = json.load(f)
        
    assets = manifest_data.get("assets", [])
    logger.info(f"Indexed assets: {[a['filename'] for a in assets]}")
    assert len(assets) >= 2, "Mock assets not properly scanned!"
    
    # Test MediaLibrary searching
    lib = MediaLibrary("dbz_verse")
    matches = lib.search_assets(["vegeta", "proud"])
    logger.info(f"Found matches for 'vegeta': {[m['filename'] for m in matches]}")
    assert len(matches) > 0, "Asset search failed!"

    # -------------------------------------------------------------
    # TEST 3: Meme Compositor
    # -------------------------------------------------------------
    logger.info("\n--- TEST 3: Media Library Meme Compositor (Static meme cards) ---")
    
    # We will use our newly generated consistent Maya portrait as the B-roll background
    output_meme = config.OUTPUTS_DIR / "test_maya_meme.png"
    text = "When the compiler errors are finally gone but you don't know why it works"
    
    logger.info(f"Composing meme card to: {output_meme}")
    MediaLibraryCompositor.generate_meme(
        background_image_path=str(output_portrait),
        text=text,
        output_path=str(output_meme),
        watermark="@maya.tech"
    )
    
    assert output_meme.exists(), "Meme card was not generated!"
    logger.info(f"Verified! Meme card successfully compiled at: {output_meme}")

    # -------------------------------------------------------------
    # TEST 4: Video Generator (Split-Screen Composites)
    # -------------------------------------------------------------
    logger.info("\n--- TEST 4: Split-Screen Faceless Reel Compositing ---")
    
    # 1. Synthesize edge-tts voiceover
    audio_path = str(config.OUTPUTS_DIR / "test_faceless_tts.wav")
    narration_script = "Vegeta is the goat of all anime history. Change my mind."
    logger.info(f"Narrating speech voiceover: '{narration_script}'")
    await generate_speech(narration_script, "en-US-GuyNeural", audio_path)
    
    # 2. Get high-precision word timestamps
    logger.info("Transcribing voiceover via Deepgram...")
    words = await VideoGenerator.transcribe_audio_deepgram(audio_path)
    
    # 3. Get mock assets
    # For testing video stacking on CPU, we will use the portrait as the top half (to simulate a static reaction show asset)
    # and download B-roll vertical footage from Pexels for the bottom gameplay half
    logger.info("Downloading bottom gameplay B-roll loop...")
    gameplay_clips = await VideoGenerator.fetch_pexels_stock_videos("gaming gameplay", max_duration=10.0)
    
    if gameplay_clips:
        bottom_gameplay = gameplay_clips[0]
        output_video_reel = str(config.OUTPUTS_DIR / "test_split_screen_faceless_reel.mp4")
        
        logger.info(f"Rendering split-screen composite Reel to: {output_video_reel}...")
        await VideoGenerator.compose_faceless_reel(
            show_asset_path=str(output_portrait), # top half image B-roll B-roll B-roll
            gameplay_video_path=bottom_gameplay,    # bottom B-roll gameplay B-roll B-roll
            audio_path=audio_path,
            words=words,
            output_mp4_path=output_video_reel
        )
        
        assert os.path.exists(output_video_reel), "Split screen reel was not compiled!"
        logger.info(f"Verified! Premium faceless split-screen reel rendered successfully at: {output_video_reel}")
    else:
        logger.warning("No gameplay clips available to perform split-screen video test. Skipping video render test.")

    logger.info("\n=== ALL PHASE 4 TESTS COMPLETED SUCCESSFULLY! ===")

if __name__ == "__main__":
    asyncio.run(run_phase4_tests())
