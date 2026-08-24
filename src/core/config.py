import os
import yaml
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Base directories
BASE_DIR = Path(__file__).resolve().parent.parent.parent
CONFIG_DIR = BASE_DIR / "config"
DATA_DIR = BASE_DIR / "data"
OUTPUTS_DIR = BASE_DIR / "outputs"
LOGS_DIR = BASE_DIR / "logs"
SESSIONS_DIR = DATA_DIR / "sessions"
REFS_DIR = DATA_DIR / "refs"
LORAS_DIR = DATA_DIR / "loras"
MEDIA_LIBRARY_DIR = DATA_DIR / "media_library"
SPRITES_DIR = DATA_DIR / "sprites"

# Ensure directories exist
for directory in [DATA_DIR, OUTPUTS_DIR, LOGS_DIR, SESSIONS_DIR, REFS_DIR, LORAS_DIR, MEDIA_LIBRARY_DIR, SPRITES_DIR]:
    directory.mkdir(exist_ok=True, parents=True)

class Config:
    BASE_DIR = BASE_DIR
    CONFIG_DIR = CONFIG_DIR
    DATA_DIR = DATA_DIR
    OUTPUTS_DIR = OUTPUTS_DIR
    LOGS_DIR = LOGS_DIR
    SESSIONS_DIR = SESSIONS_DIR
    REFS_DIR = REFS_DIR
    LORAS_DIR = LORAS_DIR
    MEDIA_LIBRARY_DIR = MEDIA_LIBRARY_DIR
    SPRITES_DIR = SPRITES_DIR

    @staticmethod
    def load_characters():
        yaml_path = CONFIG_DIR / "characters.yaml"
        if not yaml_path.exists():
            return {}
        with open(yaml_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
            return data.get("characters", {})

    @staticmethod
    def load_settings():
        yaml_path = CONFIG_DIR / "pipeline_settings.yaml"
        if not yaml_path.exists():
            return {}
        with open(yaml_path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}

    @staticmethod
    def save_settings(settings: dict):
        yaml_path = CONFIG_DIR / "pipeline_settings.yaml"
        with open(yaml_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(settings, f, default_flow_style=False, sort_keys=False)

    # API Keys
    GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
    GROQ_API_KEY = os.getenv("GROQ_API_KEY")
    CEREBRAS_API_KEY = os.getenv("CEREBRAS_API_KEY")
    SAMBANOVA_API_KEY = os.getenv("SAMBANOVA_API_KEY")
    OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
    NOUS_API_KEY = os.getenv("NOUS_API_KEY")
    LEONARDO_API_KEY = os.getenv("LEONARDO_API_KEY")
    PEXELS_API_KEY = os.getenv("PEXELS_API_KEY")
    DEEPGRAM_API_KEY = os.getenv("DEEPGRAM_API_KEY")
    
    # LoRA / Publishing keys
    TENSOR_ART_API_KEY = os.getenv("TENSOR_ART_API_KEY")
    FAL_API_KEY = os.getenv("FAL_API_KEY")
    CIVITAI_API_KEY = os.getenv("CIVITAI_API_KEY")
    WEBHOOK_URL = os.getenv("WEBHOOK_URL")

    # DB Paths
    SQLITE_DB_PATH = DATA_DIR / "content_engine.sqlite3"
    CHROMADB_PATH = DATA_DIR / "chroma_db"

    @classmethod
    def get_social_credentials(cls, char_id: str, platform: str) -> dict:
        """Returns credentials for a specific character and platform."""
        p_upper = platform.upper()
        c_upper = char_id.upper()
        if platform == "instagram":
            return {
                "username": os.getenv(f"IG_USERNAME_{c_upper}") or os.getenv("IG_USERNAME"),
                "password": os.getenv(f"IG_PASSWORD_{c_upper}") or os.getenv("IG_PASSWORD")
            }
        elif platform == "x":
            return {
                "consumer_key": os.getenv(f"X_CONSUMER_KEY_{c_upper}") or os.getenv("X_CONSUMER_KEY"),
                "consumer_secret": os.getenv(f"X_CONSUMER_SECRET_{c_upper}") or os.getenv("X_CONSUMER_SECRET"),
                "access_token": os.getenv(f"X_ACCESS_TOKEN_{c_upper}") or os.getenv("X_ACCESS_TOKEN"),
                "access_token_secret": os.getenv(f"X_ACCESS_TOKEN_SECRET_{c_upper}") or os.getenv("X_ACCESS_TOKEN_SECRET"),
                "bearer_token": os.getenv(f"X_BEARER_TOKEN_{c_upper}") or os.getenv("X_BEARER_TOKEN")
            }
        return {}

config = Config()
