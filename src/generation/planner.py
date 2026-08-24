import json
import logging
import datetime
from typing import List, Dict, Any, Optional
from sqlalchemy.orm import Session
from src.llm.router import router, TaskType
from src.llm.prompts import PLANNER_SYSTEM_PROMPT, WRITER_SYSTEM_PROMPT
from src.memory.manager import MemoryManager
from src.memory.db import ContentPost

logger = logging.getLogger("content_engine.planner")

class ContentPlanner:
    @staticmethod
    async def get_niche_trends(niche: str) -> List[str]:
        """Scouts current trends. 
        Replaces fragile pytrends scraping with robust LLM-driven organic trend brainstorming.
        """
        from src.core.config import config
        settings = config.load_settings()
        count = settings.get("trend_count", 3)
        temp = settings.get("planner_temperature", 0.70)
        
        logger.info(f"Simulating real-time trend intelligence for niche: {niche} (count={count})...")
        prompt = f"""Identify exactly {count} highly trending, viral topics, talking points, or news angles today (2026) for the social media niche: '{niche}'.
        Ensure these topics are realistic, culturally relevant, and highly engageable.
        Return ONLY a JSON list of strings representing the trends, for example: ["cyberpunk retro hardware modding", "why indie game devs are quitting unity", "the return of physical media"]
        Do not include any preambles, formatting markdown, or code blocks — return raw JSON only."""
        
        try:
            res = await router.generate(prompt, task=TaskType.SPEED_BATCH, temperature=temp)
            # Clean possible markdown formatting
            clean_res = res.replace("```json", "").replace("```", "").strip()
            return json.loads(clean_res)
        except Exception as e:
            logger.warning(f"Failed to generate trends: {str(e)}. Using fallback static trends.")
            return ["indie game dev struggles", "retro hardware emulation", "AI tool bloat"]

    @staticmethod
    async def generate_content_plan(db: Session, character_id: str) -> List[Dict[str, Any]]:
        """Generates a structured plan for the day's posts based on current trends and character profiles."""
        profile = MemoryManager.get_character_profile(db, character_id)
        if not profile:
            raise ValueError(f"Character {character_id} not found in database.")

        trends = await ContentPlanner.get_niche_trends(profile["role"])
        
        # Get character context
        context = MemoryManager.compile_writer_context(db, character_id, "instagram")
        
        from src.core.config import config
        settings = config.load_settings()
        posts_per_day = settings.get("posts_per_day", 2)
        temp = settings.get("planner_temperature", 0.70)
        
        prompt = f"""Trends today: {', '.join(trends)}

Review the character context and create exactly {posts_per_day} content plans for today (distribute them across different platforms like instagram or x, and types like static post, tweet, or video reel as appropriate):
Return a JSON list of objects matching this exact structure:
[
  {{
    "platform": "instagram",
    "post_type": "static",
    "topic": "Brief description of the topic matching the trend",
    "image_prompt": "Highly detailed visual description for the image generation engine (visualizing the character in their environment)",
    "visual_keywords": "3-5 comma separated keywords for stock search (e.g. 'cozy gaming setup, neon')",
    "focus_hook": "The core hook of the post"
  }}
]

Make sure it matches the character personality:
{profile['personality']}

Return ONLY raw JSON list containing exactly {posts_per_day} objects. Do not include markdown code block formatting."""

        logger.info(f"Generating content plan for {profile['name']} (posts={posts_per_day})...")
        res = await router.generate(prompt, system_prompt=PLANNER_SYSTEM_PROMPT, task=TaskType.PLANNING, temperature=temp)
        
        # Clean response
        clean_res = res.replace("```json", "").replace("```", "").strip()
        plans = json.loads(clean_res)
        
        logger.info(f"Successfully generated {len(plans)} content plans for {profile['name']}.")

        # Coerce post_type for faceless/debate accounts: the debate IS a reel.
        # The planner LLM may emit 'debate' (or other) for these; the media-gen
        # and publish steps only handle 'reel', so force it here, config-driven.
        try:
            from src.generation.sprite_reactor import SpriteReactor
            acct_conf = SpriteReactor._get_account(character_id) or {}
            is_faceless = acct_conf.get("type") == "faceless" or "debate" in str(acct_conf.get("pipeline", ""))
        except Exception:
            is_faceless = False
        if is_faceless:
            for plan in plans:
                if plan.get("platform", "").lower() == "instagram":
                    # Faceless debate accounts publish REELS only (the debate IS a reel).
                    # Coerce any instagram post_type (static/photo/tweet/debate/...) to 'reel'.
                    pt = str(plan.get("post_type", "")).lower()
                    if pt != "reel":
                        plan["post_type"] = "reel"
                        logger.info(f"Coerced faceless instagram post_type '{pt}' -> 'reel' for {character_id}")
                # Drop non-instagram platforms (e.g. x) for faceless debate accounts
                elif plan.get("platform", "").lower() not in ("instagram",):
                    plan["platform"] = "instagram"
                    logger.info(f"Forced faceless account platform -> 'instagram' for {character_id}")

        # Insert plans into SQLite queue
        queued_posts = []
        now = datetime.datetime.utcnow()
        for idx, plan in enumerate(plans):
            # Schedule posts separated by 4 hours
            scheduled_time = now + datetime.timedelta(hours=4 * (idx + 1))

            post = ContentPost(
                character_id=character_id,
                platform=plan["platform"],
                post_type=plan["post_type"],
                state="planned",
                scheduled_time=scheduled_time,
                image_prompt=plan.get("image_prompt"),
                caption=None,
                script=None
            )
            # Store plan details in post metadata or fields
            # We will use temporary SQLite columns or serialize to caption for script writer to read
            post.error_message = json.dumps(plan) # temporarily store full plan JSON in error_message column for script stage
            db.add(post)
            queued_posts.append(post)
            
        db.commit()
        return queued_posts

    @staticmethod
    async def write_queued_post(db: Session, post: ContentPost):
        """Ghostwrites the actual caption, tweet copy, or reel script for a planned post."""
        profile = MemoryManager.get_character_profile(db, post.character_id)
        context = MemoryManager.compile_writer_context(db, post.character_id, post.platform)
        
        # Deserialize plan details from the temporary error_message column.
        # (generate_content_plan stores the full plan JSON there; a post created
        # by other means may not have it — fall back to its own fields.)
        plan = {}
        if post.error_message:
            try:
                plan = json.loads(post.error_message) or {}
            except (json.JSONDecodeError, TypeError):
                plan = {}
        post.error_message = None  # clear temporary storage
        
        prompt = f"""CHARACTER CONTEXT & MEMORY SLIDING WINDOW:
{context}

PLAN DETAILS:
Topic: {plan.get('topic', post.image_prompt or post.caption or '')}
Post Type: {post.post_type}
Platform: {post.platform}
Core Hook Focus: {plan.get('focus_hook', post.image_prompt or '')}

Write the exact content to publish:
- If platform is 'x' and post_type is 'tweet': Write a highly engaging tweet (under 280 characters). Make it raw, sarcastic, and funny. No intros, no generic hashtags, no quotes. Just the raw tweet.
- If platform is 'instagram' and post_type is 'static': Write a compelling caption. Start with a strong hook, write 2-3 short paragraphs in character, add a call-to-action question, and end with 5 relevant hashtags.
- If post_type is 'reel': Write a 30-second script for narration (~80 words). Focus heavily on visual cues and highly engaging speech.

Return ONLY the raw post content/copy. No meta text, no introductions, no formatting."""

        from src.core.config import config
        settings = config.load_settings()
        writer_temp = settings.get("writer_temperature", 0.85)

        logger.info(f"Ghostwriting {post.post_type} for {profile['name']} on {post.platform}...")
        written_content = await router.generate(
            prompt, 
            system_prompt=WRITER_SYSTEM_PROMPT, 
            task=TaskType.CREATIVE_WRITING, 
            temperature=writer_temp
        )
        
        if post.platform == "x":
            post.caption = written_content.strip()
        else:
            post.caption = written_content.strip()
            
        post.state = "scripted"
        db.commit()
        logger.info(f"Successfully scripted post {post.id}!")
