import os
import sys
import json
import asyncio
import logging
import argparse
import datetime
from pathlib import Path
from sqlalchemy.orm import Session

# Add project root to sys.path to allow execution from anywhere
project_root = Path(__file__).resolve().parent.parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from src.core.config import config
from src.memory.db import SessionLocal, ContentPost, Character, init_db
from src.memory.init_db import populate_characters
from src.generation.planner import ContentPlanner
from src.generation.qa import QualityAssessor
from src.generation.image import ImageGenerator
from src.generation.tts import generate_speech
from src.generation.video import VideoGenerator
from src.publishing.queue_manager import ContentQueueManager
from src.core.monitoring import SystemMonitor

# Setup robust logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(config.LOGS_DIR / "production_scheduler.log", encoding="utf-8")
    ]
)
logger = logging.getLogger("content_engine.scheduler")

class ProductionScheduler:
    def __init__(self, dry_run: bool = False):
        self.dry_run = dry_run
        self.db = SessionLocal()
        # Make sure character profiles are in sync
        logger.info("Syncing character configs from characters.yaml to database...")
        init_db()
        populate_characters()

    def __del__(self):
        if hasattr(self, 'db') and self.db:
            self.db.close()

    async def run(self, target_char_id: str = None):
        """Runs the complete autonomous content lifecycle for active accounts."""
        logger.info(f"=== STARTING AUTOMATED SOCIAL MEDIA LIFECYCLE (Dry Run = {self.dry_run}) ===")
        SystemMonitor.send_info("Pipeline Started", f"Starting Content Engine Lifecycle (Dry Run = {self.dry_run})")
        
        # Load active characters
        query = self.db.query(Character).filter(Character.status == "active")
        if target_char_id:
            query = query.filter(Character.id == target_char_id)
        active_characters = query.all()

        if not active_characters:
            logger.warning("No active characters found to process.")
            SystemMonitor.send_warning("No Active Characters", "The scheduler ran but found no active character configurations.")
            return

        logger.info(f"Loaded {len(active_characters)} active accounts: {[c.name for c in active_characters]}")

        for char in active_characters:
            try:
                # Step 0: Evolve the character's weekly narrative arc so the writer's
                # sliding-window context (compile_writer_context) has fresh seeds/events.
                # This is what makes content "follow the character's narrative" instead of
                # being disconnected one-offs. Runs weekly; cheap if no new posts/events.
                try:
                    from src.scheduling.evolve_arcs import ArcEvolver
                    await ArcEvolver.evolve_character_arc(char.id)
                except Exception as arc_err:
                    logger.warning(f"Arc evolution skipped for {char.id}: {arc_err}")
                await self._process_character(char)
            except Exception as e:
                import traceback
                tb = traceback.format_exc()
                logger.error(f"Error processing character '{char.id}': {str(e)}", exc_info=True)
                SystemMonitor.send_error(
                    f"Character Lifecycle Failed", 
                    f"Autonomous pipeline encountered a failure for character '{char.name}' ({char.id}): {str(e)}",
                    character_id=char.id,
                    traceback=tb
                )

        # Step 6: Publish due posts
        logger.info("\n=============================================================")
        logger.info("PUBLISHING QUEUE PROCESSOR")
        logger.info("=============================================================")
        published_count = 0
        try:
            queue_mgr = ContentQueueManager(self.db, dry_run=self.dry_run)
            published_count = queue_mgr.process_publishing_queue()
        except Exception as e:
            import traceback
            tb = traceback.format_exc()
            logger.error(f"Publishing failed: {str(e)}", exc_info=True)
            SystemMonitor.send_error("Publishing Stage Failed", f"Encountered error while processing due posts: {str(e)}", traceback=tb)
            
        logger.info(f"Published {published_count} posts during this lifecycle run.")
        SystemMonitor.send_info("Pipeline Complete", f"Autonomous social media lifecycle complete. Published {published_count} posts.")
        logger.info("=== AUTOMATED SOCIAL MEDIA LIFECYCLE RUN COMPLETE ===")

    async def _process_character(self, char: Character):
        logger.info(f"\n=============================================================")
        logger.info(f"PROCESSING ACCOUNT: {char.name} ({char.id})")
        logger.info(f"=============================================================")

        # Find if we already have planned or scripted posts queued up for this character
        # that haven't been completed yet.
        pending_posts = (
            self.db.query(ContentPost)
            .filter(ContentPost.character_id == char.id)
            .filter(ContentPost.state.in_(["planned", "scripted"]))
            .all()
        )

        if not pending_posts:
            logger.info(f"No pending posts in queue for {char.name}. Planning a new calendar cycle...")
            # Step 1 & 2: Trend Scouting & Content Planning
            pending_posts = await ContentPlanner.generate_content_plan(self.db, char.id)
            logger.info(f"Generated {len(pending_posts)} new planned posts in queue.")

        # Step 3: Ghostwrite/Script each post
        for post in pending_posts:
            if post.state == "planned":
                logger.info(f"Ghostwriting copy for post {post.id} ({post.post_type} on {post.platform.upper()})...")
                await ContentPlanner.write_queued_post(self.db, post)
                self.db.refresh(post)

        # Step 4: Run through Quality Gate (QA)
        approved_posts = []
        for post in pending_posts:
            if post.state == "scripted":
                if post.post_type == "reel":
                    # Reels are written + QA'd by run_reel_pipeline (audio WER +
                    # sprite confidence), not the text-only assessor. Approve
                    # straight through to media generation.
                    approved_posts.append(post)
                    continue
                logger.info(f"Evaluating quality for post {post.id} ({post.platform}:{post.post_type})...")
                passed = await QualityAssessor.assess_post(self.db, post)
                if passed:
                    self.db.refresh(post)
                    approved_posts.append(post)
                else:
                    logger.warning(f"Post {post.id} failed QA. Rescripting/holding.")

        # Step 5: High-fidelity Media Asset Generation
        for post in approved_posts:
            if post.state in ("scripted", "staged"):  # Staged by QA, needs media assets
                try:
                    await self._generate_media_assets(char, post)
                except Exception as e:
                    logger.error(f"Failed to generate media assets for post {post.id}: {str(e)}", exc_info=True)

    async def _generate_media_assets(self, char: Character, post: ContentPost):
        logger.info(f"Producing media asset for post {post.id} ({post.post_type})...")
        base_filename = f"post_{post.id}_{char.id}"
        # Find matching character config by matching its "id" field
        characters_dict = config.load_characters()
        char_yaml = {}
        for c_key, c_val in characters_dict.items():
            if c_val.get("id") == char.id:
                char_yaml = c_val
                break
        
        # Check if faceless or standard character
        is_faceless = "media_library" in char_yaml
        
        if post.post_type in ("static", "photo"):
            if is_faceless:
                # Faceless account: overlay text on curated screenshot
                img_path = str(config.OUTPUTS_DIR / f"{base_filename}_meme.png")
                await ImageGenerator.generate_faceless_static(char_yaml, post.caption, img_path)
                post.media_path = img_path
                post.media_type = "photo"
            else:
                # Standard character: run visual identity + LoRA inference
                img_path = str(config.OUTPUTS_DIR / f"{base_filename}_lora.png")
                # Expand topic/hook as prompt details
                plan_details = {}
                if post.image_prompt:
                    prompt = post.image_prompt
                else:
                    prompt = f"digital influencer portrait of {char.name}, retro gamer style, retro background"
                
                await ImageGenerator.generate_character_portrait(char_yaml, prompt, img_path)
                post.media_path = img_path
                post.media_type = "photo"

        elif post.post_type == "quote_card":
            # Pillow quote card (all accounts)
            img_path = str(config.OUTPUTS_DIR / f"{base_filename}_quote.png")
            # First sentence or full caption depending on length
            quote_text = post.caption.split("\n\n")[0]
            await ImageGenerator.generate_quote_card(quote_text, char.id, img_path)
            post.media_path = img_path
            post.media_type = "photo"

        elif post.post_type == "reel":
            # Vertical Reel Video Composition — routed through the emotion-aware
            # self-improving pipeline. It owns plan -> voice(RVC) -> sprite ->
            # Whisper QA -> retry, and auto-selects the topic + character host
            # from this account's declared themes + roster (Gap 1).
            from src.generation.reel_pipeline import run_reel_pipeline, select_post

            video_output_path = str(config.OUTPUTS_DIR / f"{base_filename}_reel.mp4")

            # Decide the post: if the planner already produced a caption, derive
            # a topic from it; otherwise let the pipeline auto-select from the
            # account's theme list. Either way the pipeline writes the final
            # script + picks the character lens.
            sel = select_post(char.id)
            topic = (post.image_prompt or post.caption or sel["topic"]).strip()
            character_key = sel["character_key"]
            angle = sel.get("angle")

            logger.info(f"Reel for {char.id}: topic={topic!r} character={character_key} angle={angle!r}")
            res = await run_reel_pipeline(
                char.id,
                character_key=character_key,
                topic=topic,
                output_path=video_output_path,
                num_lines=3,
                max_retries=3,
                angle=angle,
            )

            if not res["passed"]:
                # QA gate failed after all retries — do NOT stage a bad reel.
                logger.error(f"Reel QA failed for post {post.id}: {res.get('error')}")
                post.state = "failed"
                post.error_message = f"reel QA failed: {res.get('error')}"
                self.db.commit()
                return

            # Backfill the caption with the generated script so the publish
            # record + downstream text-QA reflect what was actually said.
            script_lines = res.get("script") or []
            post.caption = " ".join(ln.get("text", "") for ln in script_lines)
            post.script = post.caption
            post.media_path = res["path"]
            post.media_type = "video"
            logger.info(f"Reel rendered: {res['path']} (wer={res['qa_report'].get('wer') if res['qa_report'] else None})")
        
        elif post.platform == "x" and post.post_type == "tweet":
            # Tweets can be text-only, or optionally attach character's generated portrait
            post.media_type = "text"
            logger.info("X Tweet is copy-only. Visual media not required.")

        # -------------------------------------------------------------
        # Post-Production Media QA Validation Checks
        # -------------------------------------------------------------
        if post.post_type == "reel":
            logger.info("Executing automated video QA validation checks...")
            video_path = post.media_path

            if not video_path or not os.path.exists(video_path):
                raise FileNotFoundError(f"Compiled Reel video file was not found at: {video_path}")

            file_size_mb = os.path.getsize(video_path) / (1024 * 1024)
            logger.info(f"Video file size: {file_size_mb:.2f} MB")
            if file_size_mb < 0.05:  # minimum 50KB for vertical reels
                raise ValueError(f"Compiled video file is suspiciously small ({file_size_mb:.2f} MB). It might be corrupted.")

            # Audio/video sync was already validated by the pipeline's Whisper QA
            # gate (it re-transcribes the final mp4 and checks WER). A light
            # dimension sanity check confirms the reel is vertical 1080x1920.
            try:
                import subprocess
                cmd = ["ffprobe", "-v", "error", "-select_streams", "v:0",
                       "-show_entries", "stream=width,height", "-of", "csv=p=0", video_path]
                out = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True)
                dims = out.stdout.strip().replace("\r", "")
                if dims != "1080,1920":
                    logger.warning(f"Reel dimensions unexpected: {dims} (expected 1080,1920)")
            except Exception as e:
                logger.warning(f"Dimension verification skipped: {str(e)}")

        # Staging: Transition post state to staged!
        post.state = "staged"
        self.db.commit()
        logger.info(f"Media produced successfully! Post #{post.id} is now STAGED and ready to publish.")

async def main():
    parser = argparse.ArgumentParser(description="Autonomous Social Media Content Engine")
    parser.add_argument("--character", type=str, help="Specific character ID to process")
    parser.add_argument("--dry-run", action="store_true", help="Simulate publishing and asset uploads")
    parser.add_argument("--daemon", action="store_true", help="Run continuously in background daemon mode")
    parser.add_argument("--interval", type=int, default=1800, help="Daemon sleep interval in seconds (default: 1800)")
    args = parser.parse_args()

    scheduler = ProductionScheduler(dry_run=args.dry_run)
    
    if args.daemon:
        logger.info(f"Starting Content Engine in DAEMON mode (interval = {args.interval}s)...")
        while True:
            try:
                await scheduler.run(target_char_id=args.character)
            except Exception as e:
                logger.error(f"Critical error in scheduler daemon cycle: {str(e)}", exc_info=True)
            logger.info(f"Sleeping for {args.interval}s before next autonomous run cycle...")
            await asyncio.sleep(args.interval)
    else:
        await scheduler.run(target_char_id=args.character)

if __name__ == "__main__":
    asyncio.run(main())
