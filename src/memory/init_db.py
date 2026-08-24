import json
import logging
from src.core.config import config
from src.memory.db import init_db, SessionLocal, Character

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("content_engine.init_db")


def _account_themes(info: dict) -> list:
    """Derives the `themes` list for a Character row.

    Persona accounts use `themes:`; faceless accounts use `topics:` for the
    same concept (the subject space the account revolves around). Normalize
    both into a list.
    """
    if info.get("themes"):
        return info["themes"]
    if info.get("topics"):
        return info["topics"]
    return []


def _account_voice(info: dict) -> str:
    """Derives a top-level `voice` for a Character row.

    Faceless accounts (dbz_verse, familyguy_verse, stoic_verse, ...) declare
    voice per-host under `characters.*.tts_voice` and have NO top-level `voice`.
    The Character table requires `voice`, so fall back to the first host's
    tts_voice (or a sane default) — the reel pipeline uses the per-host voice
    anyway, so this is only a placeholder for the ORM row.
    """
    if info.get("voice"):
        return info["voice"]
    for ch in (info.get("characters") or {}).values():
        if ch.get("tts_voice"):
            return ch["tts_voice"]
    return "en-US-GuyNeural"


def populate_characters():
    """Reads characters from YAML config and syncs them into SQLite database."""
    characters_config = config.load_characters()
    if not characters_config:
        logger.warning("No characters config found in characters.yaml!")
        return

    db = SessionLocal()
    try:
        for char_id, info in characters_config.items():
            existing_char = db.query(Character).filter(Character.id == info["id"]).first()
            if existing_char:
                logger.info(f"Updating existing character in DB: {info['name']}")
                existing_char.name = info["name"]
                existing_char.status = info.get("status", "active")
                existing_char.role = info["role"]
                existing_char.personality = info.get("personality", "")
                existing_char.visual_keywords = info.get("visual_keywords", "")
                existing_char.voice = _account_voice(info)
                existing_char.themes = json.dumps(_account_themes(info))
                existing_char.reel_style = info.get("reel_style")
                continue

            # Add new character
            logger.info(f"Adding new character to DB: {info['name']}")
            new_char = Character(
                id=info["id"],
                name=info["name"],
                status=info.get("status", "active"),
                role=info["role"],
                personality=info.get("personality", ""),
                visual_keywords=info.get("visual_keywords", ""),
                voice=_account_voice(info),
                themes=json.dumps(_account_themes(info)),
                reel_style=info.get("reel_style")
            )
            db.add(new_char)
        db.commit()
        logger.info("Database sync complete!")
    except Exception as e:
        db.rollback()
        logger.error(f"Error syncing characters to database: {str(e)}")
        raise e
    finally:
        db.close()

if __name__ == "__main__":
    logger.info("Initializing SQLite database tables...")
    init_db()
    logger.info("Tables created. Syncing character configs...")
    populate_characters()
    logger.info("Database successfully prepared.")
