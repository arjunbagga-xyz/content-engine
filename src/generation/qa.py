import json
import logging
from sqlalchemy.orm import Session
from src.llm.router import router, TaskType
from src.llm.prompts import QA_SYSTEM_PROMPT
from src.memory.manager import MemoryManager
from src.memory.db import ContentPost

logger = logging.getLogger("content_engine.qa")

class QualityAssessor:
    @staticmethod
    async def assess_post(db: Session, post: ContentPost) -> bool:
        """Evaluates a scripted post against voice, engagement, continuity, and safety.
        Returns True if it passes, False if it fails.
        """
        profile = MemoryManager.get_character_profile(db, post.character_id)
        if not profile:
            raise ValueError(f"Character {post.character_id} not found in database.")

        # -------------------------------------------------------------
        # Programmatic Prohibited AI Buzzwords Check
        # -------------------------------------------------------------
        BUZZWORDS = [
            "delve", "testament", "tapestry", "beacon", "unravel", "elevate", 
            "furthermore", "moreover", "leverage", "robust", "synergy", "pivotal", 
            "nestled", "whispers", "dynamic", "game-changer", "demystify", "revolutionize"
        ]
        caption_lower = (post.caption or "").lower()
        found_buzzwords = [w for w in BUZZWORDS if w in caption_lower]

        from src.core.config import config
        settings = config.load_settings()
        threshold = settings.get("qa_threshold", 7.0)
        max_retries = settings.get("max_retries", 3)

        if found_buzzwords:
            post.retry_count += 1
            revision_note = f"Failed programmatic buzzword check. Contains prohibited AI slang: {found_buzzwords}. Rewrite using a natural, human conversational style without cliché clinical words."
            logger.warning(f"Post {post.id} FAILED programmatic buzzword check (Retry #{post.retry_count}). Found: {found_buzzwords}")
            
            if post.retry_count >= max_retries:
                post.state = "failed"
                post.error_message = f"Failed QA {max_retries} times. Revision notes: {revision_note}"
            else:
                post.state = "planned"  # Send back to planner to be rewritten
                # Store the revision notes so the writer knows what to fix!
                post.error_message = json.dumps({
                    "topic": f"Rewrite of failed post. Fix voice/engagement.",
                    "focus_hook": f"Fix hook and body. Feedback: {revision_note}"
                })
            db.commit()
            return False

        prompt = f"""POST DETAILS:
Platform: {post.platform}
Post Type: {post.post_type}
Content Copy: 
---
{post.caption}
---

CHARACTER PERSONALITY:
{profile['personality']}

TASK:
Score the content above based on the Quality Assurance criteria.
The passing overall_score threshold is {threshold}/10. If the overall_score is >= {threshold}, set 'pass' to true, otherwise false.
Return ONLY raw JSON block matching this structure:
{{
  "voice_score": 8,
  "engagement_score": 7,
  "continuity_score": 9,
  "safety_score": 10,
  "overall_score": 8.5,
  "pass": true,
  "revision_notes": ""
}}

Make sure you return raw JSON without any markdown formatting or preambles."""

        logger.info(f"Assessing quality for post {post.id} ({post.character_id} on {post.platform}) with threshold {threshold}...")
        try:
            res = await router.generate(prompt, system_prompt=QA_SYSTEM_PROMPT, task=TaskType.QA_SCORING, temperature=0.3)
            # Clean markdown code block wraps
            clean_res = res.replace("```json", "").replace("```", "").strip()
            score_data = json.loads(clean_res)
            
            # Enforce the custom dynamic threshold as well
            overall_score = score_data.get("overall_score", 0.0)
            is_passed = score_data.get("pass", False) and (overall_score >= threshold)
            
            logger.info(f"Post {post.id} QA Assessment Results:")
            logger.info(f"Overall Score: {overall_score}/10 | Custom Threshold: {threshold} | Pass: {is_passed}")
            
            if is_passed:
                # GUARD: never stage a post that has no usable media file.
                # (Posts must be generated before QA; if media is missing the
                # generation step failed and the post must be marked failed,
                # not staged for publishing with no video.)
                import os as _os
                if not (post.media_path and _os.path.exists(post.media_path)):
                    post.state = "failed"
                    post.error_message = f"QA passed but no media file (path={post.media_path!r})"
                    db.commit()
                    logger.error(f"Post {post.id} QA passed but has no media; marked FAILED (not staged).")
                    return False
                post.state = "staged"  # Staged for publishing!
                post.error_message = None
                db.commit()
                logger.info(f"Post {post.id} PASSED QA and is STAGED for publishing!")
                return True
            else:
                post.retry_count += 1
                logger.warning(f"Post {post.id} FAILED QA (Retry #{post.retry_count}). Notes: {score_data.get('revision_notes', '')}")
                
                if post.retry_count >= max_retries:
                    post.state = "failed"
                    post.error_message = f"Failed QA {max_retries} times. Revision notes: {score_data.get('revision_notes', '')}"
                    logger.error(f"Post {post.id} exceeded max QA retries. Marked as FAILED.")
                else:
                    post.state = "planned"  # Send back to planner to be rewritten
                    # Store the revision notes so the writer knows what to fix!
                    post.error_message = json.dumps({
                        "topic": f"Rewrite of failed post. Fix voice/engagement.",
                        "focus_hook": f"Fix hook and body. Feedback: {score_data.get('revision_notes', '')}"
                    })
                    
                db.commit()
                return False
        except Exception as e:
            logger.error(f"QA Assessment crashed for post {post.id}: {str(e)}")
            # Fail CLOSED: do not publish unvetted content. Hold it for review instead.
            post.state = "held"
            post.error_message = f"QA module error (held for review): {str(e)}"
            db.commit()
            return False


class VisualQA:
    @staticmethod
    async def assess_image(image_path: str, character_config: dict, base_prompt: str) -> tuple:
        """
        Evaluates a generated character portrait image using Gemini Vision.
        Returns (passed: bool, score: float, reasons: str, prompt_adjustments: str)
        """
        char_name = character_config.get("name", "Unknown")
        role = character_config.get("role", "")
        vid = character_config.get("visual_identity", {})
        trigger = vid.get("trigger_word", "")
        hair = vid.get("hair", "")
        eyes = vid.get("eyes", "")
        skin = vid.get("skin", "")
        build = vid.get("build", "")
        style = vid.get("style", "")
        distinguishing = vid.get("distinguishing", "")
        age = vid.get("age_look", "")
        ethnicity = vid.get("ethnicity_look", "")

        prompt = f"""You are the Visual Quality Assurance Director for a premium AI Creator Network.
Your task is to critically analyze this generated portrait of the character '{char_name}' ({role}) to ensure absolute photorealism, consistency, and alignment with their visual identity.

STRICT CHARACTER VISUAL IDENTITY ANCHORS:
- Trigger word: {trigger}
- Hair: {hair}
- Eyes: {eyes}
- Skin: {skin}
- Build: {build}
- Clothing Style: {style}
- Distinguishing Features: {distinguishing}
- Age & Ethnicity: {age} / {ethnicity}

EVALUATION SCORING CRITERIA:
1. PHOTOGRAPHIC REALISM (1-10): The image MUST look like a real smartphone photo, candid snapshot, or polaroid. If it looks like a 3D model, CGI render, digital drawing/illustration, painting, cartoon, has smooth plastic/CGI skin textures, or artificial studio lighting, score it <= 6.0.
2. VISUAL IDENTITY MATCH (1-10): Does the character in the image have the correct hair color and style, eye details, clothes, and distinguishing elements listed above?
3. ANATOMY & ARTIFACTS (1-10): Check for double eyebrows, extra or missing fingers, warped glasses, merged objects, or generic AI deformations.

DECISION RULE:
- To PASS, all individual scores must be >= 8.0.
- If it fails, suggest precise adjustments to inject into the text prompt for the next attempt (e.g. "Add more grain, remove smooth CGI highlights, change eyes to hazel").

You MUST return your response strictly as a JSON block with this exact structure:
{{
  "photorealism_score": 8.5,
  "anchor_score": 9.0,
  "anatomy_score": 8.0,
  "overall_score": 8.5,
  "pass": true,
  "reasons": "Explain what you see and what matches or fails.",
  "prompt_adjustments": ""
}}
Do not write any markdown wrappers (no ```json) or explanations outside the JSON object. Return raw JSON text only."""

        logger.info(f"Running Gemini Visual QA on generated image for {char_name}: {image_path}...")
        try:
            res = await router.generate_vision(prompt, image_path, mime_type="image/png" if image_path.endswith(".png") else "image/jpeg")
            # Clean potential formatting wrappers
            clean_res = res.replace("```json", "").replace("```", "").strip()
            qa_data = json.loads(clean_res)
            
            photoreal = qa_data.get("photorealism_score", 0.0)
            anchor = qa_data.get("anchor_score", 0.0)
            anatomy = qa_data.get("anatomy_score", 0.0)
            overall = qa_data.get("overall_score", 0.0)
            passed = qa_data.get("pass", False) and (photoreal >= 8.0) and (anchor >= 8.0) and (anatomy >= 8.0)
            
            logger.info(f"Visual QA result: passed={passed}, overall_score={overall} (photoreal={photoreal}, anchor={anchor}, anatomy={anatomy})")
            return passed, overall, qa_data.get("reasons", ""), qa_data.get("prompt_adjustments", "")
        except Exception as e:
            # Fail CLOSED: a crashed vision call must NOT auto-publish. Hold for review.
            logger.error(f"Visual QA crashed with error: {str(e)}. Failing closed (image held).")
            return False, 0.0, f"Visual QA system error: {str(e)}", ""
