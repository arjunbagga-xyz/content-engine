import os
import sys
import yaml
import logging
import requests
import datetime
import asyncio
from pathlib import Path
from typing import Dict, List, Any, Optional
from fastapi import FastAPI, Request, HTTPException, Form, BackgroundTasks
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

# Add project root to sys.path
project_root = Path(__file__).resolve().parent.parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from src.core.config import config
from src.memory.db import SessionLocal, ContentPost, Character, NarrativeEvent, ArcSummary
from src.memory.init_db import populate_characters
from src.publishing.publisher import PublisherRouter
from src.jobs.registry import get_job as _get_job
from src.generation.planner import ContentPlanner
from src.generation.qa import QualityAssessor
from src.generation.media_library import ManifestBuilder

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("content_engine.dashboard")

app = FastAPI(title="AI Content Engine Cockpit")

# Ensure template and static dirs exist
TEMPLATES_DIR = Path(__file__).parent / "templates"
TEMPLATES_DIR.mkdir(exist_ok=True, parents=True)

# Mount static files for outputs directory to serve thumbnails and videos
app.mount("/outputs", StaticFiles(directory=str(config.OUTPUTS_DIR)), name="outputs")

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

# Pydantic models for request bodies
class CharacterUpdate(BaseModel):
    id: str
    name: str
    status: str
    role: str
    personality: str
    visual_keywords: str
    voice: str
    platforms: List[str]
    themes: List[str]
    reel_style: Optional[str] = None
    visual_identity: Optional[Dict[str, Any]] = None
    lora_config: Optional[Dict[str, Any]] = None
    media_library: Optional[Dict[str, Any]] = None

class EnvKeysUpdate(BaseModel):
    keys: Dict[str, str]

class NarrativeSeedCreate(BaseModel):
    character_id: str
    event_description: str
    importance: int = 5

class PostUpdate(BaseModel):
    caption: Optional[str] = None
    image_prompt: Optional[str] = None
    scheduled_time: Optional[str] = None # format YYYY-MM-DD HH:MM:SS
    state: Optional[str] = None

# Helpers for .env loading/saving
def read_env_keys() -> Dict[str, str]:
    env_path = project_root / ".env"
    keys = {}
    if env_path.exists():
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    parts = line.split("=", 1)
                    keys[parts[0].strip()] = parts[1].strip()
    return keys

def save_env_keys(new_keys: Dict[str, str]):
    env_path = project_root / ".env"
    lines = []
    existing_keys = set()
    if env_path.exists():
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                stripped = line.strip()
                if stripped and not stripped.startswith("#") and "=" in line:
                    parts = line.split("=", 1)
                    k_clean = parts[0].strip()
                    if k_clean in new_keys:
                        lines.append(f"{k_clean}={new_keys[k_clean]}\n")
                        existing_keys.add(k_clean)
                    else:
                        lines.append(line)
                else:
                    lines.append(line)
    
    # Add new ones not found in the original file
    for k, v in new_keys.items():
        if k not in existing_keys:
            lines.append(f"{k}={v}\n")
            
    with open(env_path, "w", encoding="utf-8") as f:
        f.writelines(lines)

# Dashboard Index
@app.get("/", response_class=HTMLResponse)
async def get_dashboard(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

# --- CHARACTERS API ---
@app.get("/api/characters")
async def get_characters():
    chars = config.load_characters()
    return chars

@app.post("/api/characters")
async def update_character(char_data: CharacterUpdate):
    try:
        yaml_path = config.CONFIG_DIR / "characters.yaml"
        if yaml_path.exists():
            with open(yaml_path, "r", encoding="utf-8") as f:
                full_config = yaml.safe_load(f) or {}
        else:
            full_config = {"characters": {}}
            
        if "characters" not in full_config:
            full_config["characters"] = {}
            
        # Find key name in characters.yaml by matching id
        char_key = None
        for key, details in full_config["characters"].items():
            if details.get("id") == char_data.id:
                char_key = key
                break
                
        # If new, create slug key
        if not char_key:
            char_key = f"char_{len(full_config['characters']) + 1}"
            
        # Prepare dict
        char_dict = {
            "id": char_data.id,
            "name": char_data.name,
            "status": char_data.status,
            "role": char_data.role,
            "personality": char_data.personality,
            "visual_keywords": char_data.visual_keywords,
            "voice": char_data.voice,
            "platforms": char_data.platforms,
            "themes": char_data.themes
        }
        
        if char_data.reel_style:
            char_dict["reel_style"] = char_data.reel_style
            
        if char_data.visual_identity:
            char_dict["visual_identity"] = char_data.visual_identity
            
        if char_data.lora_config:
            char_dict["lora_config"] = char_data.lora_config
            
        if char_data.media_library:
            char_dict["media_library"] = char_data.media_library
            
        full_config["characters"][char_key] = char_dict
        
        with open(yaml_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(full_config, f, default_flow_style=False, sort_keys=False)
            
        # Sync immediately into the SQLite DB
        populate_characters()
        return {"status": "success", "message": f"Character {char_data.name} saved and synced successfully."}
    except Exception as e:
        logger.error(f"Error saving character: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# --- CREDENTIALS API ---
@app.get("/api/credentials")
async def get_credentials():
    return read_env_keys()

@app.post("/api/credentials")
async def update_credentials(data: EnvKeysUpdate):
    try:
        save_env_keys(data.keys)
        # Reload env variables
        from dotenv import load_dotenv
        load_dotenv(override=True)
        return {"status": "success", "message": "Credentials updated successfully."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/credentials/test")
async def test_credential(payload: Dict[str, str]):
    provider = payload.get("provider")
    key = payload.get("key")
    
    if not provider or not key:
        raise HTTPException(status_code=400, detail="Missing provider or key")
        
    try:
        if provider == "gemini":
            url = f"https://generativelanguage.googleapis.com/v1beta/models?key={key}"
            resp = requests.get(url, timeout=5)
            if resp.status_code == 200:
                return {"status": "success", "message": "Key is valid."}
            else:
                return {"status": "error", "message": f"Failed ({resp.status_code}): {resp.text[:100]}"}
                
        elif provider == "groq":
            resp = requests.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {key}"},
                json={"model": "llama-3.3-70b-versatile", "messages": [{"role": "user", "content": "ping"}], "max_tokens": 1},
                timeout=5
            )
            if resp.status_code == 200:
                return {"status": "success", "message": "Key is valid."}
            else:
                return {"status": "error", "message": f"Failed ({resp.status_code}): {resp.text[:100]}"}
                
        elif provider == "openrouter":
            resp = requests.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={"Authorization": f"Bearer {key}"},
                json={"model": "meta-llama/llama-3.1-8b-instruct:free", "messages": [{"role": "user", "content": "ping"}], "max_tokens": 1},
                timeout=5
            )
            if resp.status_code == 200:
                return {"status": "success", "message": "Key is valid."}
            else:
                return {"status": "error", "message": f"Failed ({resp.status_code}): {resp.text[:100]}"}
                
        elif provider == "pexels":
            resp = requests.get(
                "https://api.pexels.com/v1/search?query=nature&per_page=1",
                headers={"Authorization": key},
                timeout=5
            )
            if resp.status_code == 200:
                return {"status": "success", "message": "Key is valid."}
            else:
                return {"status": "error", "message": f"Failed ({resp.status_code}): {resp.text[:100]}"}
                
        elif provider == "deepgram":
            resp = requests.get(
                "https://api.deepgram.com/v1/projects",
                headers={"Authorization": f"Token {key}"},
                timeout=5
            )
            if resp.status_code == 200:
                return {"status": "success", "message": "Key is valid."}
            else:
                return {"status": "error", "message": f"Failed ({resp.status_code}): {resp.text[:100]}"}
                
        # Other ones, fallback validation
        elif provider in ("tensor_art", "fal_ai", "civitai"):
            if len(key) > 8:
                return {"status": "success", "message": "Key format appears valid."}
            else:
                return {"status": "error", "message": "Key is too short."}
                
        return {"status": "error", "message": "Unsupported provider connection test."}
    except Exception as e:
        return {"status": "error", "message": f"Connection failed: {str(e)}"}

# --- NARRATIVE SEEDS API ---
@app.get("/api/narrative-seeds")
async def get_narrative_seeds():
    db = SessionLocal()
    try:
        events = db.query(NarrativeEvent).order_by(NarrativeEvent.created_at.desc()).limit(30).all()
        return [
            {
                "id": e.id,
                "character_id": e.character_id,
                "event_description": e.event_description,
                "importance": e.importance,
                "created_at": e.created_at.isoformat()
            }
            for e in events
        ]
    finally:
        db.close()

@app.post("/api/narrative-seeds")
async def create_narrative_seed(data: NarrativeSeedCreate):
    db = SessionLocal()
    try:
        new_event = NarrativeEvent(
            character_id=data.character_id,
            event_description=data.event_description,
            importance=data.importance,
            created_at=datetime.datetime.utcnow()
        )
        db.add(new_event)
        db.commit()
        return {"status": "success", "message": "Narrative seed planted successfully."}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()

# --- SCHEDULE & PIPELINE SETTINGS API ---
@app.get("/api/settings")
async def get_settings():
    return config.load_settings()

@app.post("/api/settings")
async def update_settings(settings: Dict[str, Any]):
    try:
        config.save_settings(settings)
        return {"status": "success", "message": "Pipeline settings updated."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# --- CONTENT QUEUE API ---
@app.get("/api/queue")
async def get_queue():
    db = SessionLocal()
    try:
        posts = db.query(ContentPost).order_by(ContentPost.scheduled_time.desc()).all()
        return [
            {
                "id": p.id,
                "character_id": p.character_id,
                "platform": p.platform,
                "post_type": p.post_type,
                "state": p.state,
                "scheduled_time": p.scheduled_time.strftime("%Y-%m-%d %H:%M:%S"),
                "actual_posted_time": p.actual_posted_time.strftime("%Y-%m-%d %H:%M:%S") if p.actual_posted_time else None,
                "caption": p.caption,
                "script": p.script,
                "media_path": p.media_path,
                "image_prompt": p.image_prompt,
                "platform_post_id": p.platform_post_id,
                "error_message": p.error_message,
                "retry_count": p.retry_count
            }
            for p in posts
        ]
    finally:
        db.close()

@app.patch("/api/queue/{post_id}")
async def update_queue_post(post_id: int, update: PostUpdate):
    db = SessionLocal()
    try:
        post = db.query(ContentPost).filter(ContentPost.id == post_id).first()
        if not post:
            raise HTTPException(status_code=404, detail="Post not found")
            
        if update.caption is not None:
            post.caption = update.caption
        if update.image_prompt is not None:
            post.image_prompt = update.image_prompt
        if update.state is not None:
            post.state = update.state
        if update.scheduled_time is not None:
            try:
                post.scheduled_time = datetime.datetime.strptime(update.scheduled_time, "%Y-%m-%d %H:%M:%S")
            except ValueError:
                raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD HH:MM:SS")
                
        db.commit()
        return {"status": "success", "message": f"Post {post_id} updated successfully."}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()

@app.post("/api/queue/{post_id}/approve")
async def approve_post(post_id: int):
    db = SessionLocal()
    try:
        post = db.query(ContentPost).filter(ContentPost.id == post_id).first()
        if not post:
            raise HTTPException(status_code=404, detail="Post not found")
        post.state = "staged"
        db.commit()
        return {"status": "success", "message": f"Post {post_id} staged successfully."}
    finally:
        db.close()

@app.post("/api/queue/{post_id}/hold")
async def hold_post(post_id: int):
    db = SessionLocal()
    try:
        post = db.query(ContentPost).filter(ContentPost.id == post_id).first()
        if not post:
            raise HTTPException(status_code=404, detail="Post not found")
        post.state = "held"
        db.commit()
        return {"status": "success", "message": f"Post {post_id} held successfully."}
    finally:
        db.close()

@app.post("/api/queue/{post_id}/delete")
async def delete_post(post_id: int):
    db = SessionLocal()
    try:
        post = db.query(ContentPost).filter(ContentPost.id == post_id).first()
        if not post:
            raise HTTPException(status_code=404, detail="Post not found")
        db.delete(post)
        db.commit()
        return {"status": "success", "message": f"Post {post_id} deleted successfully."}
    finally:
        db.close()

def bg_publish_post(post_id: int):
    db = SessionLocal()
    try:
        post = db.query(ContentPost).filter(ContentPost.id == post_id).first()
        if post:
            logger.info(f"Background publishing task started for post {post_id}")
            PublisherRouter.publish_post(db, post, dry_run=False)
        else:
            logger.error(f"Background publishing task failed: Post {post_id} not found.")
    except Exception as e:
        logger.error(f"Background publishing task failed for post {post_id}: {str(e)}")
    finally:
        db.close()

@app.post("/api/queue/{post_id}/publish")
async def publish_post_now(post_id: int, background_tasks: BackgroundTasks):
    db = SessionLocal()
    try:
        post = db.query(ContentPost).filter(ContentPost.id == post_id).first()
        if not post:
            raise HTTPException(status_code=404, detail="Post not found")
            
        background_tasks.add_task(bg_publish_post, post_id)
        return {"status": "success", "message": "Post publishing initiated in the background."}
    except Exception as e:
        logger.error(f"Failed to queue background publish: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to queue publishing: {str(e)}")
    finally:
        db.close()

# --- HEALTH, FAILURES, ARCS API ---
@app.get("/api/health")
async def get_health_checks():
    keys = read_env_keys()
    providers = ["gemini", "groq", "openrouter", "pexels", "deepgram"]
    results = {}
    
    for p in providers:
        key_name = f"{p.upper()}_API_KEY" if p != "pexels" and p != "deepgram" else f"{p.upper()}_API_KEY"
        key = keys.get(key_name)
        if not key:
            results[p] = "Missing Key"
            continue
            
        try:
            if p == "gemini":
                resp = requests.get(f"https://generativelanguage.googleapis.com/v1beta/models?key={key}", timeout=3)
                results[p] = "Active" if resp.status_code == 200 else "Invalid Key"
            elif p == "groq":
                resp = requests.post(
                    "https://api.groq.com/openai/v1/chat/completions",
                    headers={"Authorization": f"Bearer {key}"},
                    json={"model": "llama-3.3-70b-versatile", "messages": [{"role": "user", "content": "ping"}], "max_tokens": 1},
                    timeout=3
                )
                results[p] = "Active" if resp.status_code == 200 else "Invalid Key"
            elif p == "openrouter":
                resp = requests.post(
                    "https://openrouter.ai/api/v1/chat/completions",
                    headers={"Authorization": f"Bearer {key}"},
                    json={"model": "meta-llama/llama-3.1-8b-instruct:free", "messages": [{"role": "user", "content": "ping"}], "max_tokens": 1},
                    timeout=3
                )
                results[p] = "Active" if resp.status_code == 200 else "Invalid Key"
            elif p == "pexels":
                resp = requests.get("https://api.pexels.com/v1/search?query=nature&per_page=1", headers={"Authorization": key}, timeout=3)
                results[p] = "Active" if resp.status_code == 200 else "Invalid Key"
            elif p == "deepgram":
                resp = requests.get("https://api.deepgram.com/v1/projects", headers={"Authorization": f"Token {key}"}, timeout=3)
                results[p] = "Active" if resp.status_code == 200 else "Invalid Key"
        except Exception:
            results[p] = "Connection Error"
            
    return results

@app.get("/api/errors")
async def get_errors():
    db = SessionLocal()
    try:
        failed_posts = db.query(ContentPost).filter(
            (ContentPost.state == "failed") | (ContentPost.error_message.isnot(None))
        ).order_by(ContentPost.created_at.desc()).limit(30).all()
        return [
            {
                "id": p.id,
                "character_id": p.character_id,
                "platform": p.platform,
                "post_type": p.post_type,
                "error_message": p.error_message,
                "retry_count": p.retry_count,
                "created_at": p.created_at.isoformat()
            }
            for p in failed_posts
        ]
    finally:
        db.close()

@app.get("/api/arcs")
async def get_arcs():
    db = SessionLocal()
    try:
        summaries = db.query(ArcSummary).order_by(ArcSummary.created_at.desc()).all()
        return [
            {
                "id": s.id,
                "character_id": s.character_id,
                "summary_text": s.summary_text,
                "week_start": s.week_start.strftime("%Y-%m-%d"),
                "week_end": s.week_end.strftime("%Y-%m-%d"),
                "created_at": s.created_at.isoformat()
            }
            for s in summaries
        ]
    finally:
        db.close()

# --- TRIGGER PIPELINE GENERATION ---
async def run_pipeline_for_character(char_id: str):
    """Run generate -> publish for a character via the one-off job framework.

    Replaces the old monolithic ProductionScheduler.run(). Each job runs in this
    process (dashboard is already a server, not a furnace) and exits cleanly.
    """
    logger.info(f"Background task: Triggering generate+publish for character {char_id}")
    try:
        gen_rc = _get_job("generate").execute()
        logger.info(f"generate job rc={gen_rc}")
        pub_rc = _get_job("publish").execute()
        logger.info(f"publish job rc={pub_rc}")
        logger.info(f"Background task complete: processed character {char_id}")
    except Exception as e:
        logger.error(f"Background pipeline failed for {char_id}: {e}", exc_info=True)

@app.post("/api/generate")
async def trigger_generate(payload: Dict[str, str], background_tasks: BackgroundTasks):
    char_id = payload.get("character_id")
    if not char_id:
        raise HTTPException(status_code=400, detail="Missing character_id")
        
    db = SessionLocal()
    char = db.query(Character).filter(Character.id == char_id).first()
    db.close()
    if not char:
        raise HTTPException(status_code=404, detail="Character not found")
        
    background_tasks.add_task(run_pipeline_for_character, char_id)
    return {"status": "success", "message": f"Content generation triggered for {char_id} in the background."}

# --- MEDIA LIBRARY SCAN API ---
@app.post("/api/media-library/scan")
async def scan_media_library(payload: Dict[str, str]):
    char_id = payload.get("character_id")
    if not char_id:
        raise HTTPException(status_code=400, detail="Missing character_id")
        
    try:
        # Scan and build manifest for faceless or character library
        chars = config.load_characters()
        char_conf = {}
        for c_key, c_val in chars.items():
            if c_val.get("id") == char_id:
                char_conf = c_val
                break
        lib_conf = char_conf.get("media_library", {})
        library_dir = Path(lib_conf.get("path", f"data/media_library/{char_id}"))
        output_manifest_path = Path(lib_conf.get("manifest", f"data/media_library/{char_id}/manifest.json"))

        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, lambda: ManifestBuilder.auto_scan_and_build(library_dir, output_manifest_path))
        return {"status": "success", "message": f"Media library successfully scanned and manifest compiled for {char_id}."}
    except Exception as e:
        logger.error(f"Media library scan failed: {e}")
        raise HTTPException(status_code=500, detail=f"Scan failed: {str(e)}")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)
