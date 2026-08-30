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

        def _extract_json(text: str):
            """Pull a JSON list out of an LLM response that may include prose/markdown."""
            if not text:
                return None
            t = text.strip()
            if t.startswith("```"):
                t = t.strip("`")
                if t.lower().startswith("json"):
                    t = t[4:]
            start = t.find("[")
            end = t.rfind("]")
            if start == -1 or end <= start:
                return None
            return t[start:end + 1]

        plans = None
        last_err = None
        for attempt in range(3):
            try:
                res = await router.generate(prompt, system_prompt=PLANNER_SYSTEM_PROMPT,
                                            task=TaskType.PLANNING, temperature=temp)
                candidate = _extract_json(res)
                if candidate:
                    plans = json.loads(candidate)
                    break
                last_err = "no JSON array found in planner response"
            except Exception as e:
                last_err = e
                logger.warning(f"Planner JSON parse attempt {attempt+1} failed: {e}")
                continue

        if not plans:
            logger.error(f"Planner failed after retries ({last_err}); using safe default plan.")
            plans = [{"platform": "instagram", "post_type": "reel", "topic": "a surprising global event",
                      "image_prompt": "", "visual_keywords": "", "focus_hook": ""}
                     for _ in range(posts_per_day)]

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
            # Faceless debate accounts publish INSTAGRAM REELS only. The planner LLM may
            # emit any platform/post_type (static/tweet/debate/x/...); force every plan to
            # a single instagram reel so nothing tweets and nothing gets stuck.
            for plan in plans:
                if plan.get("platform", "").lower() != "instagram":
                    plan["platform"] = "instagram"
                    logger.info(f"Forced faceless account platform -> 'instagram' for {character_id}")
                if str(plan.get("post_type", "")).lower() != "reel":
                    pt = plan.get("post_type", "")
                    plan["post_type"] = "reel"
                    logger.info(f"Coerced faceless post_type '{pt}' -> 'reel' for {character_id}")

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

    @staticmethod
    async def generate_caption(script: str, topic: str, characters: Dict[str, str],
                               max_chars: int = 2000, max_hashtags: int = 5) -> str:
        """Generate an Instagram reel caption from the reel's transcript.

        Structure: HOOK (captivating, derived from topic or a standout moment) ->
        CONTENT (longer, names characters naturally in context) -> HASHTAGS
        (<=max_hashtags, freely chosen from the script; never generic #AI/#debate/#fyp).

        Enforces hard limits: total length <= max_chars, hashtags <= max_hashtags.
        """
        char_list = ", ".join(characters.keys())
        prompt = f"""You are writing an Instagram Reel caption for a faceless debate/reaction account.

TOPIC: {topic}
CHARACTERS IN THE REEL: {char_list}
SCRIPT (what the characters actually said in the reel):
\"\"\"
{script}
\"\"\"

Write a caption with exactly this structure:
1. HOOK: one captivating opening line that pulls the audience in. Source it from the topic or a standout, absurd, or provocative moment picked from the script. No "In this video..." filler.
2. CONTENT: 2-4 sentences of context that name the characters NATURALLY (e.g. "Tate's convinced..., Peppa fires back..."), in the context of what the debate was actually about. Do not use hashtag-style name mentions.
3. HASHTAGS: on the final line, generate hashtags for the {script} — freely decide them, but ONLY include character names and topic-derived tags. NEVER use generic buzzwords like #AI, #debate, #fyp, #viral, #content. Use at most {max_hashtags} hashtags.

Hard rules:
- Total caption must be under {max_chars} characters.
- Maximum {max_hashtags} hashtags. If you think of more, keep only the {max_hashtags} best.
- Return ONLY the caption text. No meta, no quotes around it."""

        caption = await router.generate(
            prompt,
            system_prompt="You write sharp, funny, native Instagram captions. No corporate tone, no buzzword spam.",
            task=TaskType.CREATIVE_WRITING,
            temperature=0.8,
        )
        caption = (caption or "").strip()

        # Enforce max hashtags: count trailing #tags, trim if over limit.
        import re
        tags = re.findall(r"#\w+", caption)
        if len(tags) > max_hashtags:
            # Remove all tags, re-add only the first max_hashtags found in order.
            kept = tags[:max_hashtags]
            # Strip existing tags then append kept ones on the end.
            caption_no_tags = re.sub(r"#\w+", "", caption)
            caption_no_tags = re.sub(r"\s+", " ", caption_no_tags).strip()
            caption = caption_no_tags + "\n\n" + " ".join(kept)

        # Enforce char limit (worst case hard truncate, but LLM is instructed).
        if len(caption) > max_chars:
            caption = caption[:max_chars].rsplit(" ", 1)[0].strip()

        return caption
        db.commit()
        logger.info(f"Successfully scripted post {post.id}!")
