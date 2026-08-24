import asyncio
import logging
import json
import datetime
from src.core.config import config
from src.memory.db import SessionLocal, ContentPost, Character
from src.generation.planner import ContentPlanner
from src.generation.qa import QualityAssessor
from src.generation.image import ImageGenerator
from src.generation.tts import generate_speech
from src.generation.video import VideoGenerator

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger("content_engine.dry_run")

async def run_end_to_end_pipeline():
    logger.info("=== STARTING FULL PRODUCTION DRY-RUN (END-TO-END) ===")
    db = SessionLocal()
    
    try:
        # Load the active launch character (Maya)
        char = db.query(Character).filter(Character.id == "maya_tech").first()
        if not char:
            logger.error("Active character 'maya_tech' not found in database. Run init_db.py first!")
            return
            
        logger.info(f"Loaded Character Profile: {char.name} ({char.role})")

        # -------------------------------------------------------------
        # STEP 1: REAL-TIME TREND SCOUTING & RESEARCH
        # -------------------------------------------------------------
        logger.info("\n=============================================================")
        logger.info("STEP 1: DYNAMIC TREND RESEARCH & TOPIC SCOUTING")
        logger.info("=============================================================")
        
        # Scout trends for Maya's niche
        trends = await ContentPlanner.get_niche_trends(char.role)
        logger.info(f"Identified Active 2026 Niche Trends:\n" + "\n".join([f"- {t}" for t in trends]))

        # -------------------------------------------------------------
        # STEP 2: CONTENT PLANNING (DAILY CALENDAR)
        # -------------------------------------------------------------
        logger.info("\n=============================================================")
        logger.info("STEP 2: GENERATING SYSTEM PLANS & CALENDAR")
        logger.info("=============================================================")
        
        # We manually construct a diverse daily plan containing:
        # 1. Instagram Static Post (AI Generated portrait)
        # 2. Instagram Quote Card (PIL text render)
        # 3. Instagram vertical Reel (Narrated stock B-roll video)
        # 4. X Tweet
        
        # We simulate what the planner generated, ensuring a complete test of all visual pipelines
        now = datetime.datetime.utcnow()
        sample_plans = [
            {
                "platform": "instagram",
                "post_type": "static",
                "topic": f"How to mod retro consoles using {trends[0]}",
                "image_prompt": "A close up photo of a messy workspace with a soldering iron, a disassembled translucent purple Game Boy Color, colorful wires, and modern retro-hardware modules on a dark wooden table, neon workbench lighting, cozy, high-detail",
                "visual_keywords": "handheld retro gaming, soldering workbench",
                "focus_hook": "Revealing the secret of building the ultimate translucent Game Boy"
            },
            {
                "platform": "instagram",
                "post_type": "quote_card",
                "topic": f"A funny hot take on {trends[1]}",
                "image_prompt": "",
                "visual_keywords": "",
                "focus_hook": "Sarcastic take on coffee consumption vs compile times"
            },
            {
                "platform": "instagram",
                "post_type": "reel",
                "topic": f"Why retro hardware is better than modern microtransactions",
                "image_prompt": "",
                "visual_keywords": "glitch CRT TV retro arcade scanlines neon",
                "focus_hook": "Reminding people of the beauty of complete physical games"
            },
            {
                "platform": "x",
                "post_type": "tweet",
                "topic": f"A quick take on {trends[2]}",
                "image_prompt": "",
                "visual_keywords": "",
                "focus_hook": "Short tech rant"
            }
        ]

        logger.info("Daily calendar plans built successfully. Committing to SQLite queue...")
        queued_posts = []
        for idx, plan in enumerate(sample_plans):
            post = ContentPost(
                character_id=char.id,
                platform=plan["platform"],
                post_type=plan["post_type"],
                state="planned",
                scheduled_time=now + datetime.timedelta(hours=4 * (idx + 1)),
                image_prompt=plan["image_prompt"],
                error_message=json.dumps(plan) # store temporarily for scripting stage
            )
            db.add(post)
            db.commit()
            db.refresh(post)
            queued_posts.append(post)
            
        logger.info(f"Successfully queued {len(queued_posts)} planned posts in SQLite queue.")

        # -------------------------------------------------------------
        # STEP 3: CREATIVE GHOSTWRITING & SCRIPTING
        # -------------------------------------------------------------
        logger.info("\n=============================================================")
        logger.info("STEP 3: GHOSTWRITING & STORYLINE CONTEXT SYNTHESIS")
        logger.info("=============================================================")
        
        for post in queued_posts:
            logger.info(f"\nScripting Post #{post.id} ({post.post_type} for {post.platform.upper()})...")
            await ContentPlanner.write_queued_post(db, post)
            logger.info(f"Written Content Draft:\n{post.caption}\n---")

        # -------------------------------------------------------------
        # STEP 4: AUTOMATED QUALITY GATE (QA SCORING)
        # -------------------------------------------------------------
        logger.info("\n=============================================================")
        logger.info("STEP 4: MULTI-DIMENSIONAL QUALITY SCORING (QA)")
        logger.info("=============================================================")
        
        passed_posts = []
        for post in queued_posts:
            logger.info(f"\nAssessing scripted draft Post #{post.id}...")
            passed = await QualityAssessor.assess_post(db, post)
            if passed:
                # Reload to get updated db state
                db.refresh(post)
                passed_posts.append(post)
                
        logger.info(f"\nQA Gate results: {len(passed_posts)} / {len(queued_posts)} posts passed.")

        # -------------------------------------------------------------
        # STEP 5: MEDIA PRODUCTION PIPELINE (IMAGES & VIDEO)
        # -------------------------------------------------------------
        logger.info("\n=============================================================")
        logger.info("STEP 5: HIGH-FIDELITY MEDIA PRODUCTION (IMAGES & RENDER)")
        logger.info("=============================================================")
        
        for post in passed_posts:
            logger.info(f"\nGenerating media assets for Post #{post.id} ({post.post_type})...")
            
            # Setup path
            base_filename = f"dryrun_post_{post.id}"
            
            if post.post_type == "static":
                # Pathway A: Pollinations FLUX Engine
                img_path = str(config.OUTPUTS_DIR / f"{base_filename}_flux.png")
                logger.info(f"Executing AI portrait gen for: '{post.image_prompt}'")
                await ImageGenerator.generate_ai_character_image(post.image_prompt, img_path)
                post.media_path = img_path
                logger.info(f"AI Portrait successfully rendered at: {img_path}")
                
            elif post.post_type == "quote_card":
                # Pathway B: Pillow Quote Card
                img_path = str(config.OUTPUTS_DIR / f"{base_filename}_quote.png")
                logger.info(f"Executing local branded PIL Quote Card drawing...")
                # Extract first paragraph
                first_paragraph = post.caption.split("\n\n")[0]
                await ImageGenerator.generate_quote_card(first_paragraph, char.id, img_path)
                post.media_path = img_path
                logger.info(f"Quote Card successfully drawn at: {img_path}")
                
            elif post.post_type == "reel":
                # Pathway C: Narration + Transcription + B-roll + FFmpeg vertical Reel
                logger.info("Executing complex Reel composition pipeline...")
                
                # Paths
                audio_path = str(config.OUTPUTS_DIR / f"{base_filename}_tts.wav")
                ass_path = str(config.OUTPUTS_DIR / f"{base_filename}_captions.ass")
                video_output_path = str(config.OUTPUTS_DIR / f"{base_filename}_reel.mp4")
                
                # 1. Generate Voice TTS — use FULL caption as narration (reels need ~30-60s)
                logger.info("Generating edge-tts voice file...")
                plan_details = json.loads(json.dumps(sample_plans[2])) # Retrieve visual keywords
                reel_script = post.caption or ""
                if len(reel_script) < 40:
                    reel_script = reel_script + " " + plan_details.get("focus_hook", "")
                await generate_speech(reel_script, char.voice, audio_path)
                
                # 2. Transcribe via Deepgram
                logger.info("Transcribing word-level timestamps via Deepgram...")
                words = await VideoGenerator.transcribe_audio_deepgram(audio_path)
                
                # 3. Download stock B-roll vertical video matching visual keywords
                keywords = plan_details.get("visual_keywords", "arcade neon retro")
                background_clips = await VideoGenerator.fetch_pexels_stock_videos(keywords, max_duration=15.0)
                
                if background_clips:
                    selected_bg = background_clips[0]
                    # 4. Compose final vertical reel using FFmpeg
                    logger.info("Running raw CPU FFmpeg multi-layer composer...")
                    await VideoGenerator.compose_reel(
                        background_video_path=selected_bg,
                        audio_path=audio_path,
                        words=words,
                        output_mp4_path=video_output_path
                    )
                    post.media_path = video_output_path
                    logger.info(f"Vertical Reel successfully rendered at: {video_output_path}")
                else:
                    logger.error("Failed to download stock background footage. Skipping video render.")
                    
            db.commit()
            
        logger.info("\n=============================================================")
        logger.info("DRY-RUN PRODUCTION STAGING REPORT")
        logger.info("=============================================================")
        
        # Reload staged posts to show summary
        staged_posts = db.query(ContentPost).filter(ContentPost.state == "staged").all()
        logger.info(f"Total Staged Posts ready to publish: {len(staged_posts)}")
        for p in staged_posts:
            logger.info(f"\n[STAGED POST #{p.id}]")
            logger.info(f"- Platform: {p.platform.upper()}")
            logger.info(f"- Format: {p.post_type}")
            logger.info(f"- Scheduled: {p.scheduled_time}")
            logger.info(f"- Media Asset: {p.media_path}")
            logger.info(f"- Staged Copy/Caption: \n{p.caption}\n")
            
        logger.info("\n=== END-TO-END DRY-RUN COMPLETED SUCCESSFULLY! ===")
    except Exception as e:
        logger.error(f"Dry-run crashed: {str(e)}")
        import traceback
        traceback.print_exc()
    finally:
        db.close()

if __name__ == "__main__":
    asyncio.run(run_end_to_end_pipeline())
