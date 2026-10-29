"""OCI snag-host video hosting — bypasses ngrok tunnels.

Uploads videos to snag-host (80.225.247.96) via SCP, serves them via nginx
at https://80.225.247.96/<filename>, then deletes after IG publish confirms.

Flow:
    1. upload(video_path, filename) → scp to snag-host
    2. get_url(filename) → https://80.225.247.96/<filename>
    3. delete(filename) → rm from snag-host after publish confirms
"""
import os
import subprocess
import requests
import logging
from pathlib import Path

logger = logging.getLogger("content_engine.snag_host")

SNAG_HOST = "80.225.247.96"
SNAG_USER = "ubuntu"
SNAG_KEY = (
    r"C:\Users\MSI\Downloads\ssh-x86\ssh-key-2026-07-12.key"
    if os.name == "nt"
    else str(Path.home() / ".ssh" / "snag_key")
)
SNAG_VIDEOS_DIR = "/var/www/videos"
SNAG_URL_BASE = f"http://{SNAG_HOST}"


def _ssh_base():
    return [
        "ssh", "-i", SNAG_KEY,
        "-o", "StrictHostKeyChecking=no",
        "-o", "ConnectTimeout=10",
        f"{SNAG_USER}@{SNAG_HOST}"
    ]


def _scp_base():
    return [
        "scp", "-i", SNAG_KEY,
        "-o", "StrictHostKeyChecking=no",
        "-o", "ConnectTimeout=10",
    ]


def upload(video_path: str, filename: str = None) -> str:
    """Upload video to snag-host. Returns remote path."""
    src = Path(video_path)
    if not src.exists():
        raise FileNotFoundError(video_path)

    if filename is None:
        filename = src.name

    dest = f"{SNAG_USER}@{SNAG_HOST}:{SNAG_VIDEOS_DIR}/{filename}"

    logger.info("Uploading %s to snag-host...", filename)
    cmd = _scp_base() + [str(src), dest]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)

    if result.returncode != 0:
        raise RuntimeError(f"SCP upload failed: {result.stderr}")

    logger.info("Uploaded %s to snag-host", filename)
    return f"{SNAG_VIDEOS_DIR}/{filename}"


def get_url(filename: str) -> str:
    """Get public URL for a file on snag-host."""
    return f"{SNAG_URL_BASE}/{filename}"


def delete(filename: str):
    """Delete video from snag-host."""
    logger.info("Deleting %s from snag-host", filename)
    cmd = _ssh_base() + [f"rm -f {SNAG_VIDEOS_DIR}/{filename}"]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        logger.warning("Failed to delete %s from snag-host: %s", filename, result.stderr)
    else:
        logger.info("Deleted %s from snag-host", filename)


def check_file(filename: str) -> bool:
    """Check if file exists on snag-host and is accessible via HTTP."""
    url = get_url(filename)
    try:
        r = requests.head(url, timeout=10, verify=False)
        return r.status_code == 200
    except Exception:
        return False


def ensure_nginx():
    """Ensure nginx is running on snag-host."""
    cmd = _ssh_base() + ["systemctl is-active nginx"]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
    if result.stdout.strip() != "active":
        logger.warning("nginx not active on snag-host, starting...")
        cmd = _ssh_base() + ["sudo systemctl start nginx"]
        subprocess.run(cmd, capture_output=True, text=True, timeout=15)
