import logging
import json
import datetime
from typing import List, Dict, Any, Optional
from sqlalchemy.orm import Session
from src.memory.db import Character, ContentPost, NarrativeEvent, ArcSummary

logger = logging.getLogger("content_engine.memory_manager")

class MemoryManager:
    @staticmethod
    def get_character_profile(db: Session, character_id: str) -> Optional[Dict[str, Any]]:
        """Retrieves a character's profile and returns it as a dict."""
        char = db.query(Character).filter(Character.id == character_id).first()
        if not char:
            return None
        return {
            "id": char.id,
            "name": char.name,
            "role": char.role,
            "personality": char.personality,
            "themes": json.loads(char.themes),
            "voice": char.voice,
            "visual_keywords": char.visual_keywords,
            "reel_style": char.reel_style
        }

    @staticmethod
    def get_recent_posts(db: Session, character_id: str, platform: str, limit: int = 3) -> List[Dict[str, Any]]:
        """Retrieves the last N posts published for a character on a specific platform (sliding window)."""
        posts = db.query(ContentPost).filter(
            ContentPost.character_id == character_id,
            ContentPost.platform == platform,
            ContentPost.state == "published"
        ).order_by(ContentPost.actual_posted_time.desc()).limit(limit).all()
        
        return [
            {
                "post_type": p.post_type,
                "caption": p.caption,
                "script": p.script,
                "posted_at": p.actual_posted_time.isoformat() if p.actual_posted_time else None
            }
            for p in posts
        ]

    @staticmethod
    def get_recent_events(db: Session, character_id: str, limit: int = 5) -> List[str]:
        """Retrieves the recent narrative events for continuity."""
        events = db.query(NarrativeEvent).filter(
            NarrativeEvent.character_id == character_id
        ).order_by(NarrativeEvent.created_at.desc()).limit(limit).all()
        
        return [e.event_description for e in events]

    @staticmethod
    def get_current_arc(db: Session, character_id: str) -> Optional[str]:
        """Retrieves the latest weekly arc summary for a character."""
        arc = db.query(ArcSummary).filter(
            ArcSummary.character_id == character_id
        ).order_by(ArcSummary.created_at.desc()).first()
        return arc.summary_text if arc else None

    @classmethod
    def compile_writer_context(cls, db: Session, character_id: str, platform: str) -> str:
        """Compiles sliding window posts, narrative events, and weekly arcs into a robust context string for writing prompts."""
        profile = cls.get_character_profile(db, character_id)
        if not profile:
            raise ValueError(f"Character {character_id} not found in database.")

        from src.core.config import config
        settings = config.load_settings()
        limit_posts = settings.get("memory_window_posts", 3)
        limit_events = settings.get("memory_window_events", 5)

        recent_posts = cls.get_recent_posts(db, character_id, platform, limit=limit_posts)
        recent_events = cls.get_recent_events(db, character_id, limit=limit_events)
        current_arc = cls.get_current_arc(db, character_id)

        context_parts = []
        
        # 1. Core Profile
        context_parts.append(f"CHARACTER PROFILE:")
        context_parts.append(f"Name: {profile['name']}")
        context_parts.append(f"Role: {profile['role']}")
        context_parts.append(f"Personality: {profile['personality']}")
        context_parts.append(f"Themes/Topics: {', '.join(profile['themes'])}")
        
        # 2. Current Narrative Arc
        if current_arc:
            context_parts.append(f"\nCURRENT NARRATIVE ARC:\n{current_arc}")
            
        # 3. Recent Storyline Events
        if recent_events:
            context_parts.append("\nRECENT EVENTS/PLOT HIGHLIGHTS (Latest first):")
            for idx, event in enumerate(recent_events, 1):
                context_parts.append(f"- {event}")
                
        # 4. Recent Posts (Sliding Window Memory)
        if recent_posts:
            context_parts.append(f"\nRECENT PUBLISHED POSTS ON {platform.upper()} (For voice consistency, avoid repeating topics):")
            for idx, post in enumerate(recent_posts, 1):
                context_parts.append(f"Post {idx} ({post['post_type']}):")
                if post['caption']:
                    context_parts.append(f"Caption: {post['caption']}")
                if post['script']:
                    context_parts.append(f"Script: {post['script']}")
                context_parts.append("---")
        else:
            context_parts.append(f"\nNo recent posts published on {platform.upper()} yet. This is a fresh start.")

        return "\n".join(context_parts)

    @staticmethod
    def queue_post(db: Session, character_id: str, platform: str, post_type: str, scheduled_time: datetime.datetime, image_prompt: str = None) -> ContentPost:
        """Add a planned post to the queue."""
        post = ContentPost(
            character_id=character_id,
            platform=platform,
            post_type=post_type,
            state="planned",
            scheduled_time=scheduled_time,
            image_prompt=image_prompt
        )
        db.add(post)
        db.commit()
        db.refresh(post)
        logger.info(f"Queued {post_type} post for {character_id} on {platform} scheduled at {scheduled_time}")
        return post
