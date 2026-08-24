import logging
import datetime
from sqlalchemy.orm import Session
from src.memory.db import SessionLocal, ArcSummary, NarrativeEvent, ContentPost, Character
from src.llm.router import router, TaskType

logger = logging.getLogger("content_engine.evolve_arcs")

class ArcEvolver:
    @staticmethod
    async def evolve_character_arc(character_id: str):
        """
        Gathers published post history and narrative events for the last 7 days,
        uses an LLM to evolve the character's storyline, saves the ArcSummary,
        and plants new NarrativeEvents in the database for the upcoming week.
        """
        logger.info(f"Starting weekly arc evolution for character: {character_id}")
        db: Session = SessionLocal()
        
        try:
            # 1. Fetch the character
            character = db.query(Character).filter(Character.id == character_id).first()
            if not character:
                logger.error(f"Character {character_id} not found in database.")
                return False
                
            # 2. Gather history for the last 7 days
            now = datetime.datetime.utcnow()
            seven_days_ago = now - datetime.timedelta(days=7)
            
            published_posts = db.query(ContentPost).filter(
                ContentPost.character_id == character_id,
                ContentPost.state == "published",
                ContentPost.actual_posted_time >= seven_days_ago
            ).order_by(ContentPost.actual_posted_time.asc()).all()
            
            recent_events = db.query(NarrativeEvent).filter(
                NarrativeEvent.character_id == character_id,
                NarrativeEvent.created_at >= seven_days_ago
            ).order_by(NarrativeEvent.created_at.asc()).all()
            
            # Format the context for the LLM
            history_text = ""
            if published_posts:
                history_text += "--- PUBLISHED POSTS (LAST 7 DAYS) ---\n"
                for p in published_posts:
                    time_str = p.actual_posted_time.strftime("%Y-%m-%d %H:%M")
                    history_text += f"[{time_str}] Platform: {p.platform} ({p.post_type})\n"
                    if p.caption:
                        history_text += f"Caption: {p.caption[:150]}...\n"
                    if p.script:
                        history_text += f"Script: {p.script[:150]}...\n"
                    history_text += "\n"
            else:
                history_text += "No posts were published in the last 7 days.\n\n"
                
            if recent_events:
                history_text += "--- ACTIVE NARRATIVE EVENTS (LAST 7 DAYS) ---\n"
                for e in recent_events:
                    time_str = e.created_at.strftime("%Y-%m-%d")
                    history_text += f"[{time_str}] (Importance: {e.importance}/10): {e.event_description}\n"
            else:
                history_text += "No specific narrative events were seeded in the last 7 days.\n\n"
                
            # 3. Create the prompt for story arc evolution
            system_prompt = (
                "You are the Lead Storyteller & Creative Director for a group of popular AI virtual influencers. "
                "Your job is to read their recent social media post history and narrative events, analyze the story progression, "
                "and evolve their character narrative arc into a cohesive plan for the next week. "
                "Keep the story highly authentic, micro-focused, relatable, and aligned with their core personality."
            )
            
            prompt = f"""
Character Profile:
Name: {character.name}
Role: {character.role}
Personality: {character.personality}

Recent 7-Day History & Seeding:
{history_text}

Task:
Analyze the character's journey this past week. Based on what they did, posted, and experienced:
1. Write a weekly 'Arc Summary' (2-3 paragraphs) capturing the emotional state, progress on projects, key experiences, and general direction of their life.
2. Outline exactly 3 new 'Narrative Seeds' for the next week. These should be bite-sized events or themes (e.g. "Spilled coffee on the custom PCB they're soldering", "Deep in a nostalgia rabbit-hole about 2000s flash games"). Each should have an importance rating from 1 to 10.

Format your output exactly as a JSON block with keys 'summary' and 'narrative_seeds'.
Example output:
{{
  "summary": "This week, Maya focused heavily on modding her Game Boy Color...",
  "narrative_seeds": [
    {{"description": "Struggles to find a rare replacement capacitor online, venting to followers.", "importance": 6}},
    {{"description": "Celebrates finally completing the shell swap with a gorgeous translucent neon housing.", "importance": 8}},
    {{"description": "Orders a massive pile of nostalgic retro snacks to eat during a late-night coding session.", "importance": 4}}
  ]
}}
Ensure the JSON is perfectly valid and contains no extra text outside the JSON block.
"""
            
            # 4. Invoke LLM Router (Planning Task)
            logger.info(f"Invoking LLM to evolve arc for {character_id}...")
            response_text = await router.generate(
                prompt=prompt,
                system_prompt=system_prompt,
                task=TaskType.PLANNING,
                temperature=0.7
            )
            
            # Clean response text and parse JSON
            import json
            import re
            cleaned_json = response_text.strip()
            # Handle markdown code blocks
            match = re.search(r"\{.*\}", cleaned_json, re.DOTALL)
            if match:
                cleaned_json = match.group(0)
                
            data = json.loads(cleaned_json)
            summary_text = data.get("summary", "")
            seeds = data.get("narrative_seeds", [])
            
            if not summary_text:
                raise ValueError("LLM returned empty summary text.")
                
            # 5. Save the ArcSummary
            arc_summary = ArcSummary(
                character_id=character_id,
                summary_text=summary_text,
                week_start=seven_days_ago,
                week_end=now,
                created_at=now
            )
            db.add(arc_summary)
            
            # 6. Plant new NarrativeEvents
            for seed in seeds:
                desc = seed.get("description")
                imp = seed.get("importance", 5)
                if desc:
                    new_event = NarrativeEvent(
                        character_id=character_id,
                        event_description=desc,
                        importance=imp,
                        created_at=now
                    )
                    db.add(new_event)
                    
            db.commit()
            logger.info(f"Successfully evolved arc for {character_id}. Added 1 ArcSummary and {len(seeds)} new NarrativeEvents.")
            return True
            
        except Exception as e:
            db.rollback()
            logger.error(f"Error evolving arc for {character_id}: {e}", exc_info=True)
            return False
        finally:
            db.close()

    @classmethod
    async def evolve_all_active_characters(cls):
        """Runs arc evolution for all active characters."""
        db = SessionLocal()
        try:
            active_chars = db.query(Character).filter(Character.status == "active").all()
            results = {}
            for char in active_chars:
                success = await cls.evolve_character_arc(char.id)
                results[char.id] = success
            return results
        finally:
            db.close()
