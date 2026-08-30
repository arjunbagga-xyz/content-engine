"""Job registry: name -> JobBase subclass.

New pipelines (e.g. influencer) register a module here with no core changes.
"""
from src.jobs.base import JobBase

# Lazily import to avoid importing heavy modules at registration time.
_JOBS = {}


def _register():
    from src.jobs.plan_job import PlanJob
    from src.jobs.generate_job import GenerateJob
    from src.jobs.publish_job import PublishJob
    from src.jobs.cleanup_job import CleanupJob
    from src.jobs.dispatcher import DispatchJob
    _JOBS.update({
        "plan": PlanJob,
        "generate": GenerateJob,
        "publish": PublishJob,
        "cleanup": CleanupJob,
        "dispatch": DispatchJob,
    })


def get_job(name: str) -> JobBase:
    if not _JOBS:
        _register()
    cls = _JOBS.get(name)
    if not cls:
        raise KeyError(f"Unknown job '{name}'. Known: {list(_JOBS)}")
    return cls()


def known_jobs() -> list:
    if not _JOBS:
        _register()
    return list(_JOBS)
