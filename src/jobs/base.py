"""Base class + settings loader for one-off jobs.

Design contracts (see plan):
- One job = one process = one log file = one exit code.
- Heavy work (generation) runs in the process and EXITS when done.
- Failures are local: a crash marks its post(s) failed and returns non-zero;
  it never freezes a shared orchestrator.
- Every job reads its `enabled` flag and exits 0 (no-op) if disabled.
"""
import asyncio
import logging
import os
import sys
import time
from pathlib import Path
from typing import Optional

import yaml

# Repo root = three levels up from this file (src/jobs/base.py -> src/jobs -> src -> repo)
REPO_ROOT = Path(__file__).resolve().parents[2]
SETTINGS_PATH = REPO_ROOT / "pipeline_settings.yaml"
LOG_DIR = REPO_ROOT / "logs" / "jobs"
DEFAULT_TIMEOUT_S = 2400  # 40 min hard backstop for the heaviest job (generate)


def load_settings() -> dict:
    """Load pipeline_settings.yaml; return {} if missing."""
    if not SETTINGS_PATH.exists():
        return {}
    try:
        with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except Exception as e:  # pragma: no cover - best effort
        logging.getLogger("jobs").warning("Failed to load %s: %s", SETTINGS_PATH, e)
        return {}


def job_enabled(name: str, settings: Optional[dict] = None) -> bool:
    if settings is None:
        settings = load_settings()
    jobs = settings.get("jobs", {}) or {}
    entry = jobs.get(name, {})
    if isinstance(entry, dict):
        return bool(entry.get("enabled", True))
    return bool(entry)


class JobBase:
    """Base class for all one-off jobs."""

    name = "job"
    hard_timeout_s = DEFAULT_TIMEOUT_S

    def __init__(self):
        self.logger = self._setup_logging()

    def _setup_logging(self) -> logging.Logger:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        log_file = LOG_DIR / f"{self.name}.log"
        logger = logging.getLogger(f"content_engine.job.{self.name}")
        logger.setLevel(logging.INFO)
        # Avoid duplicate handlers if re-instantiated in the same process.
        if not any(getattr(h, "baseFilename", "") == str(log_file) for h in logger.handlers):
            try:
                fh = logging.FileHandler(log_file, encoding="utf-8")
                fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
                logger.addHandler(fh)
            except Exception:
                pass
        if not any(isinstance(h, logging.StreamHandler) and not getattr(h, "baseFilename", None)
                   for h in logger.handlers):
            sh = logging.StreamHandler(sys.stdout)
            sh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
            logger.addHandler(sh)
        return logger

    # ---- off-switch ----
    def is_enabled(self) -> bool:
        return job_enabled(self.name)

    # ---- main entrypoints ----
    async def run(self, args: Optional[dict] = None) -> int:
        """Subclasses implement the actual work. Return 0 on success.

        `args` is the parsed argv dict from run_job.py (may include post_id,
        character, pipeline, topic for specific one-post invocations).
        """
        raise NotImplementedError

    async def _with_timeout(self, coro):
        return await asyncio.wait_for(coro, timeout=self.hard_timeout_s)

    def execute(self, args: Optional[dict] = None) -> int:
        """Synchronous entrypoint: honour off-switch, run, handle timeout/crash.

        `args` (dict|None) is forwarded to run() so the job can act on a SPECIFIC
        post (--post-id) rather than scanning the queue.

        Returns process exit code: 0 ok, 124 timeout, 1 failure.
        """
        if not self.is_enabled():
            self.logger.info("[%s] disabled in pipeline_settings.yaml — exiting no-op (0)", self.name)
            return 0

        self.logger.info("=== job '%s' starting ===", self.name)
        t0 = time.time()
        try:
            rc = asyncio.run(self._with_timeout(self.run(args)))
            self.logger.info("=== job '%s' finished OK in %.1fs ===", self.name, time.time() - t0)
            return rc if isinstance(rc, int) else 0
        except asyncio.TimeoutError:
            self.logger.error("=== job '%s' TIMED OUT after %ds ===", self.name, self.hard_timeout_s)
            return 124
        except Exception as e:  # local failure only — never take down an orchestrator
            self.logger.exception("=== job '%s' FAILED: %s ===", self.name, e)
            return 1


if __name__ == "__main__":
    # Allow `python -m src.jobs.base` smoke test (prints settings summary).
    s = load_settings()
    print("settings loaded:", bool(s))
    print("features:", s.get("features"))
    print("jobs:", s.get("jobs"))
