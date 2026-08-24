# Content Engine — Transfer & Run

## What's in the image (one file, not 52k loose files)
- Python 3.11-slim + ffmpeg (CPU)
- Source (`src/`, `config/`, `dashboard/`, `scratch/`, `generate_all.py`)
- Minimal deps (`requirements.docker.txt`) — heavy unused packages (torch/chromadb/rembg/opencv/moviepy) dropped

## NOT in the image (do these separately)
- `.env` — your API keys. Fill `env.example` → `.env` locally. Never bake keys into the image.
- `data/` — gameplay clips + media library images regenerate via `scratch/source_base_media.py` / `scratch/source_show_assets.py` (needs PEXELS_API_KEY).
- `outputs/` — generated content (regenerates each run).
- `ContentEngine_Backup_*.zip`, `bjj_camila_*` — unrelated / excluded.

## Recipient steps
```bash
# 1. load image
docker load -i content-engine.tar

# 2. create .env from template, fill keys
cp env.example .env
nano .env

# 3. run (dry-run, no publish)
docker run --rm --env-file .env -v $(pwd)/outputs:/app/outputs content-engine:latest

# 4. or shell in to source base media first
docker run --rm -it --env-file .env -v $(pwd)/data:/app/data content-engine:latest \
  python scratch/source_base_media.py
```

## Build (sender only)
```bash
docker build -t content-engine:latest .
docker save -o content-engine.tar content-engine:latest
# ship content-engine.tar + env.example + this file
```
