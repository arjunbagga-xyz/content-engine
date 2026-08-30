"""Tunnel management for Meta REELS publishing.

Meta's Content Publishing API fetches the video from a PUBLIC URL, so the
local http server (serving outputs/ on 127.0.0.1:8520) must be exposed via a
public tunnel.

We prefer a CLOUDFLARE NAMED TUNNEL (stable, persistent URL) over a quick
tunnel. Quick tunnels mint a new random URL every restart and their local
proxy on Windows is flaky for serving large video files to Meta's transcoder
(intermittent 502/530 -> container ERROR). A named tunnel has a FIXED URL and
a far more reliable local proxy, so publishing stops failing intermittently.

Configuration (in .env):
  CF_TUNNEL_NAME   - the named tunnel's name (e.g. "content-engine")
  CF_TUNNEL_URL    - the stable public URL for that tunnel
                     (e.g. https://ce-tunnel.yourdomain.com)
If these are absent we fall back to a quick tunnel (original behaviour), but
that path is flaky and not recommended.

This module guarantees a live tunnel + origin exist before publishing:
  * ensure_tunnel() -> ensures the static origin server and cloudflared
                       (named tunnel if configured) are running, returns the
                       public URL (or "" if it fails).
"""

import os
import re
import time
import subprocess
import logging
from pathlib import Path
from dotenv import load_dotenv

logger = logging.getLogger("content_engine.tunnel")

ROOT = Path(__file__).resolve().parent.parent.parent
CF_BIN = ROOT / "scratch" / "tools" / "cloudflared.exe"
CF_BIN_ALT = ROOT / "scratch" / "tools" / "cloudflared"
URL_FILE = ROOT / "scratch" / "cf_url.txt"
CF_LOG = ROOT / "logs" / "cf.log"
ORIGIN_PORT = 8520  # must match OfficialIGPublisher.NGROK_TUNNEL_PORT
ORIGIN_DIR = ROOT / "outputs"  # directory the publisher serves the video from

# --- load tunnel config from .env -------------------------------------------
load_dotenv(ROOT / ".env")
TUNNEL_NAME = os.environ.get("CF_TUNNEL_NAME", "")
TUNNEL_URL = os.environ.get("CF_TUNNEL_URL", "")


def _cf_binary() -> str | None:
    for cand in (str(CF_BIN), str(CF_BIN_ALT)):
        if os.path.exists(cand):
            return cand
    from shutil import which
    return which("cloudflared")


def _static_server_running() -> bool:
    import psutil
    for p in psutil.process_iter(["pid", "cmdline", "name"]):
        cl = " ".join(p.info["cmdline"] or [])
        if p.info["name"] == "python.exe" and "http.server" in cl and str(ORIGIN_PORT) in cl:
            return True
    return False


def _start_static_server() -> None:
    """Start a PERSISTENT static http server on ORIGIN_PORT serving ORIGIN_DIR.

    Uses our RangedHandler (HTTP Range support) so Meta's transcoder can fetch
    the reel via byte-range requests. A persistent server (kept alive for the
    whole publish lifetime) is far more reliable than the publisher's transient
    internal server, which Meta sometimes fetches while it is mid-(re)start.
    """
    bin_path = str(ROOT / ".venv" / "Scripts" / "python.exe")
    if not os.path.exists(bin_path):
        from shutil import which
        bin_path = which("python") or which("python3")
    # serve ORIGIN_DIR via ranged_server (supports Range; fixes 502 on large files)
    server_code = (
        "from src.publishing.ranged_server import serve;"
        "import threading;"
        f"httpd=serve({str(ORIGIN_DIR)!r},{ORIGIN_PORT});"
        "threading.Thread(target=httpd.serve_forever,daemon=True).start();"
        "import time;time.sleep(6000)"
    )
    subprocess.Popen(
        [bin_path, "-c", server_code],
        cwd=str(ROOT),
        creationflags=0x00000008,  # DETACHED_PROCESS on Windows
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    for _ in range(10):
        try:
            import socket
            s = socket.socket()
            s.connect(("127.0.0.1", ORIGIN_PORT))
            s.close()
            return
        except Exception:
            time.sleep(0.5)


def _ensure_static_server() -> None:
    if not _static_server_running():
        logger.info("Starting persistent static server on 127.0.0.1:%d ...", ORIGIN_PORT)
        _start_static_server()


def _ngrok_binary() -> str | None:
    from shutil import which
    for cand in (str(ROOT / "scratch" / "tools" / "ngrok.exe"),
                 str(ROOT / "scratch" / "tools" / "ngrok"),
                 "ngrok"):
        if os.path.exists(cand) if "/" in cand or "\\" in cand else which(cand):
            if os.path.exists(cand):
                return cand
    return which("ngrok")


def _ngrok_public_url() -> str:
    """Read the public URL from ngrok's local agent API."""
    import requests as _r
    try:
        r = _r.get("http://127.0.0.1:4040/api/tunnels", timeout=5)
        for t in r.json().get("tunnels", []):
            if t.get("public_url", "").startswith("https"):
                return t["public_url"]
    except Exception:
        pass
    return ""


def _start_ngrok_tunnel() -> str:
    """Start an ngrok HTTP tunnel to the origin port. Returns the public URL.

    ngrok gives a stable-enough public URL that Meta's transcoder can fetch
    reliably (unlike cloudflared quick tunnels, which 530 on large videos).
    """
    bin_path = _ngrok_binary()
    if not bin_path:
        raise RuntimeError("ngrok binary not found.")
    # ngrok auto-loads the authtoken from ~/.config/ngrok/ngrok.yml (set via
    # `ngrok config add-authtoken <TOKEN>`). No token in env needed.
    subprocess.Popen(
        [bin_path, "http", f"127.0.0.1:{ORIGIN_PORT}", "--log", str(CF_LOG), "--log-level", "info"],
        creationflags=0x00000008,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    for _ in range(30):
        time.sleep(1)
        url = _ngrok_public_url()
        if url:
            logger.info("ngrok tunnel up: %s", url)
            try:
                URL_FILE.write_text(url + "\n")
            except Exception:
                pass
            return url
    raise RuntimeError("ngrok tunnel URL did not appear within 30s.")


def _ngrok_running() -> bool:
    import psutil
    for p in psutil.process_iter(["name"]):
        if p.info["name"] == "ngrok.exe":
            return True
    return False


def _kill_ngrok() -> None:
    import psutil
    for p in psutil.process_iter(["pid", "name"]):
        if p.info["name"] == "ngrok.exe":
            try:
                p.kill()
            except Exception:
                pass
    time.sleep(1)
    import psutil
    for p in psutil.process_iter(["name"]):
        if p.info["name"] == "cloudflared.exe":
            return True
    return False


def _kill_cloudflared() -> None:
    import psutil
    for p in psutil.process_iter(["pid", "name"]):
        if p.info["name"] == "cloudflared.exe":
            try:
                p.kill()
            except Exception:
                pass
    time.sleep(2)


def _start_named_tunnel() -> str:
    """Start the configured named tunnel. Returns the stable TUNNEL_URL."""
    bin_path = _cf_binary()
    if not bin_path:
        raise RuntimeError("cloudflared binary not found.")
    if not TUNNEL_NAME:
        raise RuntimeError("CF_TUNNEL_NAME not set in .env; cannot start named tunnel.")
    CF_LOG.parent.mkdir(parents=True, exist_ok=True)
    subprocess.Popen(
        [bin_path, "tunnel", "run", TUNNEL_NAME,
         "--logfile", str(CF_LOG), "--loglevel", "info"],
        creationflags=0x00000008,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    # named tunnel comes up fast; verify the URL serves shortly
    for _ in range(20):
        time.sleep(1)
        if _url_reachable(TUNNEL_URL):
            logger.info("Named tunnel up: %s", TUNNEL_URL)
            URL_FILE.write_text(TUNNEL_URL + "\n")
            return TUNNEL_URL
    raise RuntimeError(f"Named tunnel {TUNNEL_NAME} did not come up / serve {TUNNEL_URL}.")


def _start_quick_tunnel() -> str:
    """FALLBACK: quick tunnel (flaky for large files). Returns the URL."""
    bin_path = _cf_binary()
    if not bin_path:
        raise RuntimeError("cloudflared binary not found.")
    URL_FILE.parent.mkdir(parents=True, exist_ok=True)
    URL_FILE.write_text("")
    proc = subprocess.Popen(
        [bin_path, "tunnel", "--url", f"http://127.0.0.1:{ORIGIN_PORT}",
         "--logfile", str(CF_LOG), "--loglevel", "info"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    url_pat = re.compile(r"https://[a-z0-9.-]+\.trycloudflare\.com")
    deadline = time.time() + 90
    while time.time() < deadline:
        line = proc.stdout.readline() if proc.stdout else ""
        if not line:
            if proc.poll() is not None:
                break
            time.sleep(0.5)
            continue
        m = url_pat.search(line)
        if m:
            url = m.group(0)
            URL_FILE.write_text(url + "\n")
            logger.info("Quick tunnel up: %s", url)
            return url
        try:
            txt = CF_LOG.read_text(encoding="utf-8", errors="ignore")
            fm = url_pat.findall(txt)
            if fm:
                url = fm[-1]
                URL_FILE.write_text(url + "\n")
                return url
        except Exception:
            pass
        time.sleep(0.5)
    raise RuntimeError("cloudflared quick tunnel URL did not appear within 90s.")


def _url_reachable(url: str) -> bool:
    """True only if the tunnel actually serves our origin (a 2xx/3xx on a
    real file). A 5xx (cloudflared 530 'origin unreachable') or 4xx means the
    tunnel is dead/stale and must be restarted."""
    if not url:
        return False
    # find a file we know exists in ORIGIN_DIR to probe
    probe = None
    for cand in ("post_25_tate_vs_peppa_reel.mp4", "post_32_tate_vs_peppa_reel.mp4"):
        if (ORIGIN_DIR / cand).exists():
            probe = cand
            break
    if not probe:
        # fall back to any mp4 in outputs
        for f in sorted(ORIGIN_DIR.glob("*.mp4")):
            probe = f.name
            break
    try:
        import requests
        r = requests.head(url + "/" + probe, timeout=10) if probe else requests.head(url, timeout=10)
        return r.status_code in (200, 206, 301, 302, 303, 307, 308)
    except Exception:
        return False


def ensure_tunnel() -> str:
    """Ensure a live public tunnel + persistent origin server exist.

    Preference order (most reliable first):
      1. Cloudflare NAMED tunnel (stable URL) — if CF_TUNNEL_NAME/URL configured.
      2. ngrok tunnel — reliable public URL for Meta's transcoder to fetch.
      3. cloudflared QUICK tunnel — flaky for large videos (530/502), last resort.
    Returns the public URL or '' on failure.
    """
    try:
        # origin first so Meta can always fetch through the tunnel
        _ensure_static_server()

        # 1. named cloudflared tunnel
        use_named = bool(TUNNEL_NAME and TUNNEL_URL)
        if use_named:
            if _cloudflared_running() and _url_reachable(TUNNEL_URL):
                return TUNNEL_URL
            _kill_cloudflared()
            try:
                return _start_named_tunnel()
            except Exception as e:
                logger.warning("named tunnel failed (%s); falling back to ngrok", e)

        # 2. ngrok tunnel (preferred fallback — reliable for video fetch)
        if _ngrok_running():
            url = _ngrok_public_url()
            if url and _url_reachable(url):
                return url
            _kill_ngrok()
        try:
            return _start_ngrok_tunnel()
        except Exception as e:
            logger.warning("ngrok tunnel failed (%s); falling back to quick cloudflared", e)

        # 3. quick cloudflared tunnel (flaky — last resort)
        url = _live_quick_url()
        if url and _url_reachable(url):
            return url
        if _cloudflared_running():
            _kill_cloudflared()
        return _start_quick_tunnel()
    except Exception as e:
        logger.error("ensure_tunnel failed: %s", e)
        return ""


def _live_quick_url() -> str:
    from src.publishing.official_publisher import _public_url
    return _public_url()


def restart_tunnel() -> str:
    """Force a tunnel restart (named preferred)."""
    _kill_cloudflared()
    return ensure_tunnel()


def stop_tunnel() -> None:
    """Tear down the tunnel + origin server. Call at the END of the publish job so
    cloudflared/ngrok + the static http.server do NOT linger (heat/resource leak fix).

    Kills cloudflared, ngrok, and the persistent python http.server bound to
    ORIGIN_PORT. Safe to call when nothing is running (no-ops).
    """
    import psutil
    # 1. Kill cloudflared
    for p in psutil.process_iter(["pid", "name"]):
        try:
            if p.info["name"] == "cloudflared.exe":
                p.kill()
        except Exception:
            pass
    # 2. Kill ngrok
    for p in psutil.process_iter(["pid", "name"]):
        try:
            if p.info["name"] == "ngrok.exe":
                p.kill()
        except Exception:
            pass
    # 3. Kill the static-origin http.server on ORIGIN_PORT (match by port, not
    #    cmdline — our server runs via ranged_server, not literal 'http.server').
    for c in psutil.net_connections(kind="tcp"):
        if c.laddr.port == ORIGIN_PORT and c.pid and c.pid != 0:
            try:
                psutil.Process(c.pid).kill()
            except Exception:
                pass
    # 4. Invalidate the cached URL so the next publish re-establishes a fresh tunnel.
    try:
        if URL_FILE.exists():
            URL_FILE.write_text("")
    except Exception:
        pass
    logger.info("stop_tunnel(): cloudflared + ngrok + origin server torn down.")
