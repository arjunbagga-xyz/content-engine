"""run_job.py — dispatch one registered one-off job (specific, not a queue blast).

The planner (6x/day) is the ONLY scheduler. It creates ScheduledJob ledger rows with
exact argv, then fires due ones via this script. Each invocation runs ONE job, often
targeting ONE post specifically:

    python run_job.py plan                                   # the 6x/day brain (no args)
    python run_job.py generate --post-id 84 --character tate_vs_peppa
    python run_job.py publish  --post-id 84 --character tate_vs_peppa
    python run_job.py cleanup
    python run_job.py --list

Generate/publish NEVER scan the queue — they act on the single --post-id passed.
"""
import sys
import os
import argparse

# Ensure repo root on sys.path (this file lives at repo root).
REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="run_job.py", description="Run one specific Content Engine job.")
    p.add_argument("job", nargs="?", help="job name: plan | dispatch | generate | publish | cleanup")
    p.add_argument("--list", "-l", action="store_true", help="list known jobs and exit")
    p.add_argument("--post-id", type=int, help="specific ContentPost id (generate/publish)")
    p.add_argument("--character", help="specific character/account id")
    p.add_argument("--pipeline", help="specific pipeline to use")
    p.add_argument("--topic", help="specific topic override")
    p.add_argument("--batch", help="batch key for the dispatcher (e.g. tate_vs_peppa:2026-08-29)")
    return p


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()

    if args.list or not args.job:
        from src.jobs.registry import known_jobs
        print("Known jobs:", ", ".join(known_jobs()))
        return 0

    try:
        from src.jobs.registry import get_job
        job = get_job(args.job)
    except KeyError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2

    # Hand the parsed args to the job so it can act on a SPECIFIC post.
    return job.execute(vars(args))


if __name__ == "__main__":
    sys.exit(main())
