"""Cleanup job — 10-day retention sweep of outputs/.

Wraps the existing scratch/cleanup_outputs.py sweep(). Runs as its own one-off job
so retention never blocks generation/publishing.
"""
from src.jobs.base import JobBase
from src.core.config import OUTPUTS_DIR


class CleanupJob(JobBase):
    name = "cleanup"

    async def run(self) -> int:
        try:
            from scratch.cleanup_outputs import sweep
        except Exception:
            import importlib.util
            spec = importlib.util.spec_from_file_location(
                "cleanup_outputs", str(OUTPUTS_DIR.parent / "scratch" / "cleanup_outputs.py"))
            sweep = importlib.util.module_from_spec(spec).sweep

        reclaimed = sweep(10, True) or 0.0
        self.logger.info("Cleanup complete: reclaimed %.2f MB from outputs/", reclaimed)
        return 0


if __name__ == "__main__":
    import sys
    sys.exit(CleanupJob().execute())
