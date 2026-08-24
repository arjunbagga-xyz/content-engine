"""Official Instagram Content Publishing API publisher (Meta-sanctioned).

Flow per account:
  1. re-encode source to a web-friendly mp4 (libx264/aac, 1080x1920)
  2. serve it on a local HTTP port + expose via ngrok tunnel
  3. create a REELS container (Meta fetches video_url)
  4. poll container until FINISHED
  5. media_publish -> live
  6. tear down tunnel + server; optionally delete the temp file

Tokens/user-ids are read from .env:
  IG_OFFICIAL_<ACCOUNT>_TOKEN, IG_OFFICIAL_<ACCOUNT>_USER_ID
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

# allow running as a script (python src/publishing/official_publisher.py)
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

logger = logging.getLogger("content_engine.official_publisher")

GRAPH = "https://graph.instagram.com/v21.0"
NGROK_TUNNEL_PORT = 8520  # local port the http.server binds; cloudflared forwards here


def _load_creds(account_id: str):
    """Read token + user_id for an account from .env via dotenv."""
    try:
        from dotenv import load_dotenv
        load_dotenv(Path(__file__).resolve().parents[2] / ".env")
    except Exception:
        pass
    token = os.environ.get(f"IG_OFFICIAL_{account_id.upper()}_TOKEN")
    uid = os.environ.get(f"IG_OFFICIAL_{account_id.upper()}_USER_ID")
    if not token or not uid:
        raise RuntimeError(
            f"Missing IG_OFFICIAL_{account_id.upper()}_TOKEN / _USER_ID in .env. "
            f"Run scratch/meta_token_setup.py first."
        )
    return token, uid


def reencode(src: str, dst: str) -> str:
    """Re-encode to a web-friendly mp4. Returns dst path."""
    if Path(dst).exists():
        return dst
    cmd = [
        "ffmpeg", "-y", "-i", src,
        "-c:v", "libx264", "-preset", "medium", "-crf", "28",
        "-pix_fmt", "yuv420p", "-movflags", "+faststart",
        "-c:a", "aac", "-b:a", "128k",
        "-vf", "scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2",
        dst,
    ]
    logger.info("Re-encoding: %s", " ".join(cmd))
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError("ffmpeg re-encode failed: " + r.stderr[-400:])
    return dst


def _public_url() -> str:
    """Read the public HTTPS URL from the cloudflared quick-tunnel log."""
    # prefer cloudflared log
    for cand in [Path("scratch/cf.log"), Path(__file__).resolve().parents[2] / "scratch" / "cf.log"]:
        if cand.exists():
            import re as _re
            txt = cand.read_text(encoding="utf-8", errors="ignore")
            m = _re.findall(r"https://[a-z0-9-]+\.trycloudflare\.com", txt)
            if m:
                return m[-1]  # most recent tunnel is alive
    # fallback: ngrok agent api
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
        src = Path(video_path)
        if not src.exists():
            raise FileNotFoundError(video_path)

        # 1. re-encode
        if reencode_first:
            web = src.parent / (src.stem + "_web.mp4")
            video_path = reencode(str(src), str(web))
            temp_path = web
        else:
            temp_path = src

        # 2. serve + expose via cloudflared (assumed already running, forwarding NGROK_TUNNEL_PORT)
        public = _public_url()
        if not public:
            raise RuntimeError(
                "ngrok not running. Start it: ngrok http 127.0.0.1:8517 "
                "(or use cloudflared). The video must be publicly reachable during processing."
            )
        fname = Path(temp_path).name
        video_url = f"{public.rstrip('/')}/{fname}"

        # start a simple http server bound to the tunnel port, serving the file dir
        server_proc = self._start_server(temp_path, NGROK_TUNNEL_PORT)

        try:
            # 3. create container
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
                raise RuntimeError(f"container create failed {c.status_code}: {c.text[:300]}")
            container_id = c.json()["id"]

            # 4. poll until FINISHED
            status = ""
            for _ in range(30):  # up to ~10 min
                s = requests.get(
                    f"{GRAPH}/{container_id}",
                    params={"fields": "status_code", "access_token": self.token},
                    timeout=30,
                ).json()
                status = s.get("status_code")
                logger.info("container %s status: %s", container_id, status)
                if status == "FINISHED":
                    break
                if status in ("ERROR", "EXPIRED"):
                    raise RuntimeError(f"container {container_id} {status}: {s}")
                time.sleep(20)
            if status != "FINISHED":
                raise RuntimeError(f"container {container_id} timed out at {status}")

            # 5. publish
            m = requests.post(
                f"{GRAPH}/{self.ig_user_id}/media_publish",
                data={"creation_id": container_id, "access_token": self.token},
                timeout=60,
            )
            if m.status_code != 200:
                raise RuntimeError(f"publish failed {m.status_code}: {m.text[:300]}")
            media_id = m.json()["id"]
            permalink = requests.get(
                f"{GRAPH}/{media_id}",
                params={"fields": "permalink", "access_token": self.token},
                timeout=30,
            ).json().get("permalink", "")
            logger.info("PUBLISHED reel %s -> %s", media_id, permalink)
            if cleanup_temp and temp_path != src and Path(temp_path).exists():
                try:
                    Path(temp_path).unlink()
                except Exception:
                    pass
            return media_id
        finally:
            # 6. teardown server
            if server_proc:
                try:
                    server_proc.shutdown()
                except Exception:
                    pass

    @staticmethod
    def _start_server(file_path: str, port: int):
        """Serve the directory containing file_path on 127.0.0.1:port with Range support."""
        from src.publishing.ranged_server import serve
        d = str(Path(file_path).parent)
        httpd = serve(d, port)
        import threading
        t = threading.Thread(target=httpd.serve_forever, daemon=True)
        t.start()
        time.sleep(1.0)
        return httpd


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)
    acct = sys.argv[1] if len(sys.argv) > 1 else "tate_vs_peppa"
    vid = sys.argv[2] if len(sys.argv) > 2 else "outputs/tvp_test.mp4"
    cap = sys.argv[3] if len(sys.argv) > 3 else "Test reel from Content Engine #faceless #debate"
    p = OfficialIGPublisher(acct)
    mid = p.post_reel(vid, cap)
    print("PUBLISHED media_id=", mid)
