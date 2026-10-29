#!/usr/bin/env python3
"""Upload video to snag-host for Meta publishing.

Usage: python upload_to_snag.py <video_path> [post_id]

Args:
    video_path: Path to the video file to upload
    post_id: Optional post ID (derived from filename if not provided)

Returns:
    Public URL of the uploaded video (e.g., http://80.225.247.96/post_X.mp4)
"""

import sys
import os
import subprocess
import json
import logging
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("snag_upload")

SSH_KEY = os.path.expanduser(r"C:\Users\MSI\Downloads\ssh-x86\ssh-key-2026-07-12.key")
SNAG_HOST = "ubuntu@80.225.247.96"
SNAG_PORT = 22
VIDEOS_DIR = "/var/www/videos"


def get_post_id(video_path: str) -> str:
    """Extract post ID from filename like 'post_255_fitness_ball_z_reel.mp4'."""
    stem = Path(video_path).stem
    # Extract number from 'post_X_...'
    if stem.startswith("post_"):
        parts = stem.split("_")
        if len(parts) >= 2 and parts[1].isdigit():
            return parts[1]
    return stem


def upload_video(video_path: str, post_id: str) -> str:
    """Upload video to snag-host via SCP.
    
    Returns:
        Public URL of the uploaded video
    """
    video_path = Path(video_path).resolve()
    if not video_path.exists():
        raise FileNotFoundError(f"Video not found: {video_path}")
    
    # Target filename on snag-host
    target_name = f"post_{post_id}.mp4"
    target_path = f"{VIDEOS_DIR}/{target_name}"
    
    # SCP upload
    logger.info(f"Uploading {video_path} -> {target_name} ({video_path.stat().st_size / 1024 / 1024:.1f} MB)")
    
    cmd = [
        "scp",
        "-i", SSH_KEY,
        "-o", "StrictHostKeyChecking=no",
        "-o", "ConnectTimeout=10",
        str(video_path),
        f"{SNAG_HOST}:{target_path}"
    ]
    
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    
    if result.returncode != 0:
        raise RuntimeError(f"SCP failed: {result.stderr}")
    
    # Verify upload
    logger.info("Verifying upload...")
    verify_cmd = [
        "ssh", "-i", SSH_KEY,
        "-o", "StrictHostKeyChecking=no",
        SNAG_HOST,
        f"ls -la {target_path}"
    ]
    
    verify_result = subprocess.run(verify_cmd, capture_output=True, text=True, timeout=15)
    
    if verify_result.returncode != 0:
        raise RuntimeError(f"Upload verification failed: {verify_result.stderr}")
    
    # Set permissions
    subprocess.run([
        "ssh", "-i", SSH_KEY,
        "-o", "StrictHostKeyChecking=no",
        SNAG_HOST,
        f"sudo chown www-data:www-data {target_path} && sudo chmod 644 {target_path}"
    ], capture_output=True, text=True, timeout=15)
    
    # Public URL (HTTP for now, HTTPS after SSL setup)
    public_url = f"http://80.225.247.96/{target_name}"
    
    logger.info(f"Uploaded to: {public_url}")
    return public_url


def delete_video(post_id: str) -> bool:
    """Delete video from snag-host after IG confirms publish."""
    target_name = f"post_{post_id}.mp4"
    target_path = f"{VIDEOS_DIR}/{target_name}"
    
    cmd = [
        "ssh", "-i", SSH_KEY,
        "-o", "StrictHostKeyChecking=no",
        SNAG_HOST,
        f"rm -f {target_path}"
    ]
    
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
    return result.returncode == 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    
    video_path = sys.argv[1]
    post_id = sys.argv[2] if len(sys.argv) > 2 else get_post_id(video_path)
    
    try:
        url = upload_video(video_path, post_id)
        print(json.dumps({"url": url, "post_id": post_id}))
    except Exception as e:
        logger.error(f"Upload failed: {e}")
        sys.exit(1)
