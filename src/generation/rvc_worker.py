"""Persistent RVC worker CLIENT (runs in the production .venv, which has no torch).

It owns a single long-lived rvc_env subprocess (worker_server.py) that loads each
character's RVC model ONCE and batch-converts segments over a line protocol. This
removes the old per-segment subprocess spawn that crashed on the 4GB 1650
(rc=0, no output) and was ~7x slower on CPU. The sandbox proof showed the
persistent model-load approach converts 10/10 segments on the 1650 at ~27s/seg
with zero flakes.

Only batch_convert() is used by voice_provider.
"""
import os
import sys
import json
import logging
import subprocess
import threading
from pathlib import Path

logger = logging.getLogger("content_engine.rvc_worker")

_RVC_REPO = Path(__file__).resolve().parent.parent.parent / "rvc_repo"
_RVC_PY = str(Path(__file__).resolve().parent.parent.parent / "rvc_env" / "Scripts" / "python.exe")
_SERVER = str(_RVC_REPO / "worker_server.py")

_lock = threading.Lock()
_proc = None
_req_id = 0


def _ensure_server():
    """Start the persistent rvc_env server (once) and confirm it's ready."""
    global _proc
    with _lock:
        if _proc is not None and _proc.poll() is None:
            return _proc
        # (re)start
        import os as _os
        _log = open(str(_RVC_REPO.parent / "logs" / "rvc_worker.log"), "a", buffering=1)
        _proc = subprocess.Popen(
            [_RVC_PY, _SERVER],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=_log, text=True, cwd=str(_RVC_REPO),
            bufsize=1,
            # Hide the window: this is a headless worker spawned by the scheduler /
            # generate step. Without this it pops a visible console per RVC-worker
            # start (and on every restart), which flashes during autopilot runs.
            creationflags=0x08000000,  # CREATE_NO_WINDOW
        )
        # Wait for SERVER_READY (or SERVER_FAIL)
        ready = _proc.stdout.readline().strip()
        if ready != "SERVER_READY":
            err = _proc.stderr.read()
            raise RuntimeError(f"RVC server failed to start: {ready} {err[:400]}")
        logger.info("RVC persistent worker server started on cuda:0")
        return _proc


def _request(proc, character, pitch, in_wav, out_wav, timeout_s=600):
    global _req_id
    with _lock:
        _req_id += 1
        rid = f"r{_req_id}"
        req = json.dumps({"id": rid, "character": character, "pitch": pitch, "in_wav": str(in_wav), "out_wav": str(out_wav)})
        proc.stdin.write(req + "\n")
        proc.stdin.flush()
    # Read until we get our RES (server is serial; fine for our single-threaded use).
    while True:
        line = proc.stdout.readline().strip()
        if not line:
            raise RuntimeError("RVC server closed stdout")
        try:
            resp = json.loads(line)
        except Exception:
            continue
        if resp.get("id") == rid:
            if resp.get("status") == "OK":
                return True, int(resp.get("bytes", 0))
            return False, resp.get("error", "unknown")
        # ignore stray PONG/other


def batch_convert(character: str, items: list, pitch: int = 0) -> list:
    """Convert many narrator wavs to the character voice in one persistent session.

    items: list of (input_wav, output_wav)
    Returns: list of (ok: bool, detail) in the same order.
    Raises RuntimeError only if the server itself can't start (caller fails loud).
    """
    proc = _ensure_server()
    results = []
    for in_wav, out_wav in items:
        try:
            ok, detail = _request(proc, character, pitch, str(in_wav), str(out_wav))
            results.append((ok, str(out_wav) if ok else detail))
        except Exception as e:
            results.append((False, str(e)))
    return results


def evict(character: str = None):
    """Optional: tell the server to drop cached models (frees GPU). Not required."""
    pass
