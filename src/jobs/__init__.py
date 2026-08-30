"""Job framework package.

Each job is a self-contained unit: one process, one log file, one exit code.
Heavy work (generation) runs inside a spawned child and exits when done — the
orchestrator never becomes a permanent furnace.

See .hermes/plans/2026-08-26_234500-async-jobs-from-monolith.md for the design.
"""
from src.jobs.base import JobBase, load_settings

__all__ = ["JobBase", "load_settings"]
