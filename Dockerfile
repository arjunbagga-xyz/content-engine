# Content Engine — container build (CPU-only, all heavy steps are remote APIs)
FROM python:3.11-slim

# ffmpeg is the only real local compute (video compositing). Everything else is HTTP to APIs.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install deps first (cached layer) — uses the slim requirements, not the full one.
COPY requirements.docker.txt /app/requirements.docker.txt
RUN pip install --no-cache-dir -r requirements.docker.txt

# Copy source + config + sprite base-media (NOT .venv, outputs, logs, .env — see .dockerignore)
COPY src/ /app/src/
COPY config/ /app/config/
COPY scratch/ /app/scratch/
COPY data/sprites/ /app/data/sprites/
COPY data/sprites_clean/ /app/data/sprites_clean/
COPY data/gameplay/ /app/data/gameplay/
COPY src/generate_all.py README.md requirements.txt pipeline_control_panel.md /app/

# Runtime data dirs (mount or let them generate)
RUN mkdir -p /app/data/gameplay /app/data/media_library /app/outputs /app/logs

ENV PYTHONPATH=/app
ENV PYTHONDONTWRITEBYTECODE=1

# Default: dry-run the full pipeline (no publish). Override CMD when deploying.
CMD ["python", "generate_all.py", "--config", "config/pipeline_settings.yaml", "--dry-run"]
