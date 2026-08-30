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
from src.publishing import tunnel as tunnel_mgr

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
    """Re-encode to a web-friendly mp4. Returns dst path.

    A stale/partial _web.mp4 from a previously-killed run must NOT be trusted:
    ffmpeg -y overwrites, but if the dst already exists we validate it has a
    moov atom + non-trivial size before reusing, otherwise re-create it.
    """
    dst_p = Path(dst)
    if dst_p.exists():
        # Validate the existing file isn't a corrupt leftover (e.g. killed mid-encode).
        if dst_p.stat().st_size > 1_000_000:  # >1MB sanity floor
            r = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=noprint_wrappers=1:nokey=1", str(dst_p)],
                capture_output=True, text=True,
            )
            if r.returncode == 0 and r.stdout.strip():
                return dst  # valid cached re-encode
        # corrupt/empty -> remove and re-create
        try:
            dst_p.unlink()
        except OSError:
            pass
    cmd = [
        "ffmpeg", "-y", "-i", src,
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "38",
        "-maxrate", "1000k", "-bufsize", "2000k",
        "-pix_fmt", "yuv420p", "-movflags", "+faststart",
        "-c:a", "aac", "-b:a", "96k",
        "-vf", "scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2",
        dst,
    ]
    logger.info("Re-encoding: %s", " ".join(cmd))
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError("ffmpeg re-encode failed: " + r.stderr[-400:])
    return dst


def _public_url() -> str:
    """Return the CURRENT public HTTPS tunnel URL.

    Priority:
      1. scratch/cf_url.txt  — a single, authoritative line written by the
         tunnel launcher (truncated on each start, so it never holds a stale URL).
      2. logs/cf.log / scratch/cf.log — parsed for the *last* trycloudflare URL
         (best-effort fallback; these logs accumulate across restarts so the
         final line is assumed to be the live tunnel).
      3. ngrok agent API — only if ngrok is the active tunnel.
    """
    root = Path(__file__).resolve().parents[2]
    # 1. authoritative single-line file from the launcher
    for cand in [root / "scratch" / "cf_url.txt", Path("scratch/cf_url.txt")]:
        if cand.exists():
            txt = cand.read_text(encoding="utf-8", errors="ignore").strip()
            import re as _re
            m = _re.search(r"https://[a-z0-9-]+\.trycloudflare\.com", txt)
            if m:
                return m.group(0)
    # 2. parse cf logs (last match = most recent tunnel)
    for cand in [root / "logs" / "cf.log", Path("logs/cf.log"),
                 root / "scratch" / "cf.log", Path("scratch/cf.log")]:
        if cand.exists():
            import re as _re
            txt = cand.read_text(encoding="utf-8", errors="ignore")
            m = _re.findall(r"https://[a-z0-9-]+\.trycloudflare\.com", txt)
            if m:
                return m[-1]
    # 3. ngrok fallback
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

        Primary path: resumable DIRECT upload to rupload.facebook.com (no public
        tunnel needed — avoids the 530/502 errors cloudflared/ngrok tunnels throw
        on large video files). Falls back to the video_url+tunnel path only if
        direct upload is unavailable.
        """
        src = Path(video_path)
        if not src.exists():
            raise FileNotFoundError(video_path)

        # 1. re-encode to a web-friendly mp4
        if reencode_first:
            web = src.parent / (src.stem + "_web.mp4")
            video_path = reencode(str(src), str(web))
            temp_path = web
        else:
            temp_path = src

        # 2. try direct upload (no tunnel)
        try:
            return self._post_reel_direct(temp_path, caption, cleanup_temp)
        except Exception as e:
            logger.warning("Direct upload failed (%s); falling back to tunnel", e)

        # 3. fallback: video_url + tunnel (start a FRESH tunnel + origin server;
        # do NOT reuse the stale cached URL from cf_url.txt — that caused 502s
        # when the cached ngrok session had already died).
        public = tunnel_mgr.ensure_tunnel()
        if not public:
            raise RuntimeError(
                "No tunnel available and direct upload failed. "
                "Start a tunnel or fix direct upload."
            )
        fname = Path(temp_path).name
        video_url = f"{public.rstrip('/')}/{fname}"
        # NOTE: ensure_tunnel() (called above) already started the persistent
        # ranged_server on ORIGIN_PORT. We do NOT start our own here (that caused
        # a port conflict with the stale server). stop_tunnel() (in publish_job's
        # finally) tears the server down after publish.
        try:
            logger.info("Creating REELS container (tunnel): %s", video_url)
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
            status = ""
            for _ in range(30):
                s = requests.get(
                    f"{GRAPH}/{container_id}",
                    params={"fields": "status_code,error_message", "access_token": self.token},
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
            # Server + tunnel are owned by ensure_tunnel/stop_tunnel; do not
            # shut them down here (publish_job's finally calls stop_tunnel).
            pass

    def _post_reel_direct(self, video_path: str, caption: str,
                          cleanup_temp: bool) -> str:
        """Resumable DIRECT upload to rupload.facebook.com (no tunnel)."""
        from pathlib import Path as _P
        vp = _P(video_path)
        size = vp.stat().st_size
        # Step 1: create resumable container
        c = requests.post(
            f"{GRAPH}/{self.ig_user_id}/media",
            json={
                "media_type": "REELS",
                "upload_type": "resumable",
                "caption": caption or "",
                "share_to_feed": "true",
            },
            headers={"Authorization": f"Bearer {self.token}",
                     "Content-Type": "application/json"},
            timeout=60,
        )
        if c.status_code != 200:
            raise RuntimeError(f"resumable container create failed {c.status_code}: {c.text[:300]}")
        container_id = c.json()["id"]
        uri = c.json().get("uri") or f"{GRAPH.replace('graph.instagram.com', 'rupload.facebook.com')}/ig-api-upload/{container_id}"
        logger.info("Resumable container %s -> %s", container_id, uri)
        # Step 2: upload bytes directly
        with open(vp, "rb") as f:
            up = requests.post(
                uri,
                headers={
                    "Authorization": f"Bearer {self.token}",
                    "offset": "0",
                    "file_size": str(size),
                    "Content-Type": "application/octet-stream",
                },
                data=f,
                timeout=300,
            )
        if up.status_code not in (200, 201) or not up.json().get("success", False):
            raise RuntimeError(f"rupload failed {up.status_code}: {up.text[:300]}")
        logger.info("Upload successful (%d bytes)", size)
        # Step 3: poll until FINISHED (read video_status too)
        status = ""
        for _ in range(40):
            s = requests.get(
                f"{GRAPH}/{container_id}",
                params={"fields": "status_code,video_status", "access_token": self.token},
                timeout=30,
            ).json()
            status = s.get("status_code")
            logger.info("container %s status: %s", container_id, status)
            if status == "FINISHED":
                break
            if status in ("ERROR", "EXPIRED"):
                raise RuntimeError(f"container {container_id} {status}: {s}")
            time.sleep(15)
        if status != "FINISHED":
            raise RuntimeError(f"container {container_id} timed out at {status}")
        # Step 4: publish
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
        if cleanup_temp and Path(video_path).exists():
            try:
                Path(video_path).unlink()
            except Exception:
                pass
        return media_id

    @staticmethod
    def _start_server(file_path: str, port: int):
        """Serve the directory containing file_path on 127.0.0.1:port with Range support.

        Robust against stale servers: any process still bound to `port` (from a
        prior publish run that crashed before teardown) is killed first, so we
        never silently serve the OLD code / a dead socket to ngrok (which caused
        persistent 502s). Raises if the port cannot be bound after cleanup.
        """
        import psutil
        for c in psutil.net_connections(kind="tcp"):
            if c.laddr.port == port and c.pid and c.pid != 0:
                try:
                    psutil.Process(c.pid).kill()
                except Exception:
                    pass
        from src.publishing.ranged_server import serve
        d = str(Path(file_path).parent)
        httpd = serve(d, port)
        import threading
        t = threading.Thread(target=httpd.serve_forever, daemon=True)
        t.start()
        time.sleep(1.0)
        # verify something is actually listening
        import socket
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        ok = s.connect_ex(("127.0.0.1", port)) == 0
        s.close()
        if not ok:
            raise RuntimeError(f"origin server failed to bind on 127.0.0.1:{port}")
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
