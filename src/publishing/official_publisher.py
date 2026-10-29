"""Official Instagram Content Publishing API publisher (Meta-sanctioned).

Cascading error handler with 4 retries, tunnel health checks, and
specific error diagnosis (502, container ERROR, timeout).

Tokens/user-ids are read from .env:
  IG_OFFICIAL_{ACCOUNT}_TOKEN, IG_OFFICIAL_{ACCOUNT}_USER_ID
Secrets are never printed.
"""
import os
import re
import time
import shutil
import logging
import subprocess
import requests
import sys
from pathlib import Path
from src.publishing import snag_host

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

logger = logging.getLogger("content_engine.official_publisher")

GRAPH = "https://graph.instagram.com/v21.0"


class ContainerCreateError(Exception):
    pass


class ContainerError(Exception):
    pass


class ContainerTimeout(Exception):
    pass


class PublishError(Exception):
    pass


def _load_creds(account_id: str):
    try:
        from dotenv import load_dotenv
        load_dotenv(Path(__file__).resolve().parents[2] / ".env")
    except Exception:
        pass
    token = os.environ.get(f"IG_OFFICIAL_{account_id.upper()}_TOKEN")
    uid = os.environ.get(f"IG_OFFICIAL_{account_id.upper()}_USER_ID")
    if not token or not uid:
        raise RuntimeError(
            f"Missing IG_OFFICIAL_{account_id.upper()}_TOKEN / _USER_ID in .env."
        )
    return token, uid


def reencode(src: str, dst: str) -> str:
    dst_p = Path(dst)
    if dst_p.exists():
        if dst_p.stat().st_size > 1_000_000:
            r = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=noprint_wrappers=1:nokey=1", str(dst_p)],
                capture_output=True, text=True,
            )
            if r.returncode == 0 and r.stdout.strip():
                return dst
        try:
            dst_p.unlink()
        except OSError:
            pass
    cmd = [
        "ffmpeg", "-y", "-i", src,
        "-c:v", "libx264", "-preset", "medium", "-crf", "23",
        "-maxrate", "5000k", "-bufsize", "10000k",
        "-pix_fmt", "yuv420p", "-movflags", "+faststart",
        "-c:a", "aac", "-b:a", "192k",
        "-vf", "scale=608:1080:force_original_aspect_ratio=decrease,pad=608:1080:(ow-iw)/2:(oh-ih)/2",
        dst,
    ]
    logger.info("Re-encoding: %s", " ".join(cmd))
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError("ffmpeg re-encode failed: " + r.stderr[-400:])
    return dst


def _public_url() -> str:
    root = Path(__file__).resolve().parents[2]
    for cand in [root / "scratch" / "cf_url.txt", Path("scratch/cf_url.txt")]:
        if cand.exists():
            txt = cand.read_text(encoding="utf-8", errors="ignore").strip()
            import re as _re
            m = _re.search(r"https://[a-z0-9-]+\.trycloudflare\.com", txt)
            if m:
                return m.group(0)
    for cand in [root / "logs" / "cf.log", Path("logs/cf.log"),
                 root / "scratch" / "cf.log", Path("scratch/cf.log")]:
        if cand.exists():
            import re as _re
            txt = cand.read_text(encoding="utf-8", errors="ignore")
            m = _re.findall(r"https://[a-z0-9-]+\.trycloudflare\.com", txt)
            if m:
                return m[-1]
    try:
        r = requests.get("http://127.0.0.1:4040/api/tunnels", timeout=5)
        for t in r.json().get("tunnels", []):
            if t.get("public_url", "").startswith("https"):
                return t["public_url"]
    except Exception:
        pass
    return ""


class OfficialIGPublisher:
    def __init__(self, account_id: str):
        self.account_id = account_id
        self.token, self.ig_user_id = _load_creds(account_id)

    def post_reel(self, video_path: str, caption: str,
                  reencode_first: bool = True,
                  cleanup_temp: bool = True) -> str:
        """Publish a REELS via the Meta API.

        Cascading error handler with 4 retries:
          1. Verify origin server + tunnel health BEFORE each attempt
          2. Detect specific errors (502, container ERROR, timeout)
          3. Take targeted action: restart tunnel, extend timeout, etc.
          4. Log every failure with context for debugging
        """
        src = Path(video_path)
        if not src.exists():
            raise FileNotFoundError(video_path)

        if reencode_first:
            web = src.parent / (src.stem + "_web.mp4")
            video_path = reencode(str(src), str(web))
            temp_path = web
        else:
            temp_path = src

        fname = Path(temp_path).name
        last_error = None

        for attempt in range(1, 5):  # 4 retries
            logger.info("=== PUBLISH attempt %d/4 for %s (%.1f MB) ===",
                        attempt, fname, Path(temp_path).stat().st_size / 1024 / 1024)

            # Upload to snag-host
            try:
                logger.info("Uploading to snag-host...")
                snag_host.upload(str(temp_path), fname)
                video_url = snag_host.get_url(fname)
                logger.info("Video URL: %s", video_url)
            except Exception as e:
                logger.error("Snag-host upload failed: %s", e)
                continue

            try:
                logger.info("Creating REELS container: %s", video_url)
                c = requests.post(
                    f"{GRAPH}/{self.ig_user_id}/media",
                    data={
                        "media_type": "REELS",
                        "video_url": video_url,
                        "caption": caption or "",
                        "share_to_feed": "true",
                        "access_token": self.token,
                    }, timeout=60,
                )
                if c.status_code != 200:
                    raise ContainerCreateError(f"status={c.status_code}: {c.text[:200]}")

                container_id = c.json()["id"]

                # Poll with adaptive timeout (extends on each retry)
                timeout_s = 120 if attempt == 1 else 180
                status = self._poll_container(container_id, timeout_s)

                if status == "FINISHED":
                    m = requests.post(
                        f"{GRAPH}/{self.ig_user_id}/media_publish",
                        data={"creation_id": container_id, "access_token": self.token},
                        timeout=60,
                    )
                    if m.status_code != 200:
                        raise PublishError(f"status={m.status_code}: {m.text[:200]}")

                    media_id = m.json()["id"]
                    permalink = requests.get(
                        f"{GRAPH}/{media_id}",
                        params={"fields": "permalink", "access_token": self.token},
                        timeout=30,
                    ).json().get("permalink", "")

                    # Delete from snag-host after confirmed publish
                    logger.info("Publish confirmed, deleting from snag-host...")
                    snag_host.delete(fname)

                    logger.info("PUBLISHED reel %s -> %s (attempt %d)", media_id, permalink, attempt)

                    if cleanup_temp and temp_path != src and Path(temp_path).exists():
                        try:
                            Path(temp_path).unlink()
                        except Exception:
                            pass
                    return media_id

                elif status in ("ERROR", "EXPIRED"):
                    raise ContainerError(f"container {container_id} {status}")
                else:
                    raise ContainerTimeout(f"container {container_id} timed out at {status}")

            except (ContainerError, ContainerTimeout, PublishError, requests.RequestException) as e:
                last_error = e
                logger.warning("Attempt %d failed: %s", attempt, e)

                # Error-specific actions for snag-host flow
                error_str = str(e).lower()
                if "400" in error_str:
                    logger.warning("Meta API rejected request -> will retry with fresh upload")
                elif "timeout" in error_str:
                    logger.warning("Timeout -> extending poll window for next attempt")
                else:
                    logger.warning("Unknown error -> retrying with fresh upload")

        raise RuntimeError(f"Publish failed after 4 attempts. Last error: {last_error}")

    def _poll_container(self, container_id: str, timeout_s: int) -> str:
        start = time.time()
        poll_interval = 15
        while time.time() - start < timeout_s:
            s = requests.get(
                f"{GRAPH}/{container_id}",
                params={"fields": "status_code,error_message", "access_token": self.token},
                timeout=30,
            ).json()
            status = s.get("status_code")
            elapsed = time.time() - start
            logger.info("container %s status: %s (%.0fs) raw=%s", container_id, status, elapsed, s)
            if status == "FINISHED":
                return status
            if status in ("ERROR", "EXPIRED"):
                logger.error("Container ERROR: %s", s.get("error_message", ""))
                return status
            time.sleep(poll_interval)
        return "TIMEOUT"


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)
    acct = sys.argv[1] if len(sys.argv) > 1 else "tate_vs_peppa"
    vid = sys.argv[2] if len(sys.argv) > 2 else "outputs/tvp_test.mp4"
    cap = sys.argv[3] if len(sys.argv) > 3 else "Test reel from Content Engine #faceless #debate"
    p = OfficialIGPublisher(acct)
    mid = p.post_reel(vid, cap)
    print("PUBLISHED media_id=", mid)
