"""PromptBuilder + lean FacelessMemory.

Design (per the account-config contract):
- Prompt templates live in YAML as strings with {{variables}}.
- PromptBuilder fills them from a context dict at runtime. Variables can be
  plugged by the trend scout ({{topic}}), memory system ({{recent_topics}}),
  and the debate loop ({{speaker}}, {{persona}}, {{ctx}}, ...).
- FacelessMemory is LEAN: it records/de-duplicates topics per account using the
  shared ContentPost table. It does NOT touch the influencer arc/narrative
  machinery (no NarrativeEvent / ArcSummary). Faceless accounts have no storyline.
"""
import random
import re
from typing import Dict, Any, List, Optional
from datetime import datetime

# Variables the PromptBuilder knows how to supply generically. Account-specific
# vars (e.g. {{trading_note}}) are passed through from the caller's context.
_VAR_RE = re.compile(r"\{\{(\w+)\}\}")


def fill_template(template: str, context: Dict[str, Any]) -> str:
    """Fill a {{var}} template from context. Unknown vars become '' (never crash)."""
    def repl(m):
        key = m.group(1)
        val = context.get(key, "")
        return "" if val is None else str(val)
    return _VAR_RE.sub(repl, template)


class PromptBuilder:
    """Builds per-turn prompts for an account from its YAML prompt_templates."""

    def __init__(self, account_conf: Dict[str, Any]):
        self.conf = account_conf
        self.templates: List[str] = list(account_conf.get("prompt_templates", [])) or [self._default()]

    @staticmethod
    def _default() -> str:
        return ("TOPIC: {{topic}}\nTHIS SPEAKER: {{speaker}} — {{persona}}\n"
                "WHAT'S BEEN SAID:\n{{ctx}}\nDeliver ONE short take as {{speaker}} in their "
                "exact voice. {{trading_note}} Output ONLY the line, no quotes.")

    def build(self, context: Dict[str, Any]) -> str:
        """Pick a template (random for variety) and fill it."""
        tmpl = random.choice(self.templates)
        return fill_template(tmpl, context)


class FacelessMemory:
    """Lean per-account topic memory. No narrative arcs (faceless accounts have none).

    Records debated topics so future runs can avoid repeats. Uses the shared
    ContentPost table (topic stored as a tag on the post). Reads/writes via the
    memory manager so it integrates with the existing DB without duplicating it.
    """

    def __init__(self, account_id: str, db_session=None):
        self.account_id = account_id
        self._session = db_session

    def recent_topics(self, limit: int = 8) -> List[str]:
        """Return recent debated topics for this account (most recent first)."""
        try:
            from src.memory.manager import MemoryManager
            from src.memory.db import SessionLocal
            session = self._session or SessionLocal()
            posts = MemoryManager.get_recent_posts(session, self.account_id, "instagram", limit=limit)
            topics = [p.get("topic") for p in posts if p.get("topic")]
            if not self._session:
                session.close()
            return topics
        except Exception as e:
            import logging
            logging.getLogger("content_engine.prompt_builder").warning(
                f"[faceless-mem] recent_topics failed ({e}); returning []")
            return []

    def record_topic(self, topic: str, platform: str = "instagram") -> None:
        """Record a debated topic for this account (best-effort; never blocks)."""
        try:
            from src.memory.manager import MemoryManager
            from src.memory.db import SessionLocal
            session = self._session or SessionLocal()
            MemoryManager.queue_post(
                session, self.account_id, platform, "debate",
                datetime.utcnow(), image_prompt=None)
            if not self._session:
                session.close()
        except Exception as e:
            import logging
            logging.getLogger("content_engine.prompt_builder").warning(
                f"[faceless-mem] record_topic failed ({e})")
