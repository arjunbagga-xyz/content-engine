import asyncio
import logging
from src.core.config import config
from src.memory.db import SessionLocal, ContentPost
from src.generation.planner import ContentPlanner
from src.generation.qa import QualityAssessor
from src.generation.image import ImageGenerator

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("content_engine.test_generation")

async def test_full_generation():
    logger.info("Initializing full Content Generation Pipeline test...")
    db = SessionLocal()
    
    try:
        # 1. Generate content plans for the launch character Maya
        logger.info("\n--- STEP 1: GENERATING DAILY PLAN ---")
        queued_posts = await ContentPlanner.generate_content_plan(db, "maya_tech")
        logger.info(f"Queued {len(queued_posts)} planned posts in database.")
        
        # Reload posts from DB to make sure session has clean states
        post_ids = [p.id for p in queued_posts]
        db.close() # reset session
        
        # 2. Iterate through queued posts, write copy, run QA, and generate visual media
        for post_id in post_ids:
            db = SessionLocal()
            post = db.query(ContentPost).filter(ContentPost.id == post_id).first()
            if not post:
                continue
                
            logger.info(f"\n--- STEP 2: WRITING POST COPY (ID: {post.id} | Platform: {post.platform}) ---")
            await ContentPlanner.write_queued_post(db, post)
            logger.info(f"Written Caption/Tweet: \n{post.caption}")
            
            logger.info(f"\n--- STEP 3: RUNNING QA GATE (ID: {post.id}) ---")
            passed = await QualityAssessor.assess_post(db, post)
            
            # Reset post state to staged if we want to force proceed with image gen for testing
            post = db.query(ContentPost).filter(ContentPost.id == post_id).first()
            
            if post.state == "staged" and post.platform == "instagram":
                logger.info(f"\n--- STEP 4: GENERATING IMAGE MEDIA (ID: {post.id}) ---")
                
                # Pick visual pathway based on post type/niche
                output_image_path = str(config.OUTPUTS_DIR / f"test_post_{post.id}.png")
                
                # Since we want to test all pathways:
                if post.id % 2 == 0:
                    logger.info("Testing Quote Card Renderer pathway...")
                    await ImageGenerator.generate_quote_card(
                        text=post.caption.split("\n\n")[0], # use first paragraph
                        character_id="maya_tech",
                        output_path=output_image_path
                    )
                else:
                    logger.info("Testing Pollinations FLUX engine pathway...")
                    # Build custom prompt using character personality + plan keywords
                    prompt = f"cyberpunk female game developer, {post.image_prompt or 'neon, retro computers'}"
                    await ImageGenerator.generate_ai_character_image(
                        prompt=prompt,
                        output_path=output_image_path
                    )
                    
                logger.info(f"Visual media successfully saved to: {output_image_path}")
            
            db.close()
            
        logger.info("\n=== CONTENT GENERATION PIPELINE TEST SUCCESSFUL! ===")
    except Exception as e:
        logger.error(f"Pipeline test failed: {str(e)}")
        import traceback
        traceback.print_exc()
    finally:
        db.close()

if __name__ == "__main__":
    asyncio.run(test_full_generation())
