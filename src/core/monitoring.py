import os
import requests
import logging
from datetime import datetime
from src.core.config import config

logger = logging.getLogger("monitoring")

class SystemMonitor:
    @staticmethod
    def _send_webhook(payload: dict):
        webhook_url = os.getenv("WEBHOOK_URL") or config.WEBHOOK_URL
        if not webhook_url:
            logger.info("Webhook URL not set, skipping alert dispatch.")
            return False
        
        try:
            response = requests.post(webhook_url, json=payload, timeout=10)
            if response.status_code in [200, 204]:
                return True
            else:
                logger.error(f"Failed to send webhook, status code: {response.status_code}, response: {response.text}")
                return False
        except Exception as e:
            logger.error(f"Exception sending webhook: {e}")
            return False

    @classmethod
    def send_info(cls, title: str, message: str, character_id: str = None):
        """Sends an informational alert (Blue)."""
        logger.info(f"Monitor INFO: {title} - {message}")
        
        embed = {
            "title": f"ℹ️ {title}",
            "description": message,
            "color": 3447003,  # Blue
            "timestamp": datetime.utcnow().isoformat() + "Z"
        }
        
        if character_id:
            embed["fields"] = [{"name": "Character", "value": character_id, "inline": True}]
            
        payload = {"embeds": [embed]}
        return cls._send_webhook(payload)

    @classmethod
    def send_warning(cls, title: str, message: str, character_id: str = None):
        """Sends a warning alert (Yellow/Orange)."""
        logger.warning(f"Monitor WARNING: {title} - {message}")
        
        embed = {
            "title": f"⚠️ {title}",
            "description": message,
            "color": 16753920,  # Orange
            "timestamp": datetime.utcnow().isoformat() + "Z"
        }
        
        if character_id:
            embed["fields"] = [{"name": "Character", "value": character_id, "inline": True}]
            
        payload = {"embeds": [embed]}
        return cls._send_webhook(payload)

    @classmethod
    def send_error(cls, title: str, message: str, character_id: str = None, traceback: str = None):
        """Sends a critical error alert (Red)."""
        logger.error(f"Monitor ERROR: {title} - {message} \nTraceback: {traceback}")
        
        embed = {
            "title": f"🚨 {title}",
            "description": message,
            "color": 15158332,  # Red
            "timestamp": datetime.utcnow().isoformat() + "Z"
        }
        
        fields = []
        if character_id:
            fields.append({"name": "Character", "value": character_id, "inline": True})
        if traceback:
            # Discord embed fields support up to 1024 characters
            tb_val = traceback[:1000] + "..." if len(traceback) > 1000 else traceback
            fields.append({"name": "Traceback", "value": f"```python\n{tb_val}\n```", "inline": False})
            
        if fields:
            embed["fields"] = fields
            
        payload = {"embeds": [embed]}
        return cls._send_webhook(payload)
