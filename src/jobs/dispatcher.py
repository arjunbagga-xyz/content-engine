"""Dispatcher — ONE-SHOT batch drainer, invoked by the planner's schedule.

NOT a daemon. NOT a frequent poller. The planner (6x/day) writes ScheduledJob
rows (generate/publish, status='queued', grouped by `batch`) and one 'dispatch'
row. When that dispatch row fires, `run_job.py dispatch --batch KEY` runs THIS
once: it drains the batch.

What draining means:
  * GENERATE queue: run queued generate jobs ONE-BY-ONE (sequential, never
    parallel — one render at a time). Each generate subprocess renders one reel.
    On success the post flips to 'staged' and its publish job is ARMED at the
    planner's planned fire_at (not a hardcoded +2min). On failure, retry the
    generate up to GENERATE_RETRIES with backoff; if exhausted -> post 'failed'.
  * PUBLISH queue: run queued publish jobs whose fire_at <= now, ONE-BY-ONE.
    On failure, retry up to PUBLISH_RETRIES with backoff; exhausted -> 'failed'.

Everything is logged (logs/dispatch.log) so the planner can audit what happened
on its next run. The planner reconciles: a 'fired' row with a dead heartbeat is
reclaimed as 'missed' and re-queued.

This replaces the old fire_due() single-unit-per-planner-run model: the dispatcher
now executes a WHOLE batch per invocation, and the planner controls timing.
"""
import os
import sys
import json
import time
import logging
import datetime
import subprocess

from src.memory.db import SessionLocal, ScheduledJob, ContentPost

logger = logging.getLogger("content_engine.dispatcher")

# Global mutex: only ONE dispatcher may drain a batch at a time. The planner
# writes per-slot dispatch rows, and a generate can run >10 min, so two
# `run_job.py dispatch` invocations WILL overlap in production and collide on
# the same jobs/posts (duplicate renders, stolen 'staged' posts, nothing
# publishes). This lock makes a second concurrent dispatcher exit immediately
# instead of fighting the first. Cross-process via an atomic directory create
# (os.mkdir fails if it already exists on every OS — more reliable than
# file-locking on Windows), with a liveness check so a crashed dispatcher
# doesn't leave a stale lock forever.
_LOCK_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "logs", "dispatcher.lock")


def _pid_alive(pid: int) -> bool:
    """Return True only if `pid` is a LIVE process that is actually one of ours
    (a dispatcher / run_job invocation). The naive ctypes OpenProcess check returns
    True for ANY pid with a handle — including a PID recycled to an unrelated process
    after our dispatcher died — which would make a dead lock look alive and wedge the
    whole loop forever. So we verify via psutil that the process exists and is running
    our code."""
    if pid <= 0:
        return False
    try:
        import psutil
        if not psutil.pid_exists(pid):
            return False
        proc = psutil.Process(pid)
        if proc.status() == psutil.STATUS_ZOMBIE:
            return False
        cmd = " ".join(proc.cmdline() or [])
        # Only trust the lock if it's genuinely a dispatcher/planner process.
        if "run_job" in cmd or "dispatch" in cmd or "dispatcher" in cmd:
            return True
        # PID exists but isn't ours (recycled) -> treat as dead so the lock is stolen.
        return False
    except Exception:
        return False


# Max time a single dispatcher drain is expected to take. A lock older than this
# is treated as STALE and stolen — even if its PID still appears alive (e.g. a
# killed-by-timeout process whose PID lingered, or a Task Scheduler kill that
# left the dir behind). 90 min >> worst-case render (GEN_TIMEOUT_S=3600 headroom
# is generous; a drain that runs 90 min has clearly wedged).
_LOCK_TTL_S = 90 * 60


def _force_remove_lock() -> bool:
    """Best-effort removal of the lock dir. On Windows a dir deleted by a killed
    process can linger in a 'pending deletion' state where os.path.exists is True but
    rmtree/rmdir fail with a permissions error until the OS releases the handle.
    Renaming it aside (which usually succeeds) then removing the renamed target is the
    reliable workaround. Retries a few times. Returns True only if the dir is gone."""
    import shutil
    for attempt in range(5):
        # 1) Try to rename it aside — works even on pending-delete dirs.
        try:
            if os.path.exists(_LOCK_DIR):
                _trash = _LOCK_DIR + f".old.{os.getpid()}.{attempt}"
                os.rename(_LOCK_DIR, _trash)
        except Exception:
            pass
        # 2) Remove the (possibly renamed) dir.
        for cand in (f for f in [_LOCK_DIR] if os.path.exists(f)):
            try:
                if os.path.isdir(cand) and not os.path.islink(cand):
                    shutil.rmtree(cand, ignore_errors=True)
                else:
                    os.remove(cand)
            except Exception:
                pass
        if not os.path.exists(_LOCK_DIR):
            return True
        time.sleep(0.3)
    return not os.path.exists(_LOCK_DIR)


def _acquire_lock() -> bool:
    """Return True if THIS process now holds the dispatcher lock. False if
    another dispatcher is already running (caller should exit without draining)."""
    import shutil
    acquired = False
    for attempt in range(3):
        try:
            os.mkdir(_LOCK_DIR)  # atomic: fails if dir exists
            acquired = True
            break
        except FileExistsError:
            # Lock dir exists — is the owner still alive AND recent? If not, steal.
            try:
                pid_file = os.path.join(_LOCK_DIR, "pid")
                info_file = os.path.join(_LOCK_DIR, "mtime")
                age_s = None
                if os.path.exists(info_file):
                    age_s = time.time() - os.path.getmtime(info_file)
                live = False
                old_pid = 0
                if os.path.exists(pid_file):
                    with open(pid_file) as f:
                        old_pid = int(f.read().strip() or "0")
                    live = bool(old_pid) and _pid_alive(old_pid)
                # Re-entrancy: if the lock is already held by THIS process, we own
                # it — return True instead of deadlocking on our own pid. (The planner
                # can invoke dispatch_due more than once per run; the lock dir may also
                # linger from a prior invocation of the same process.)
                if old_pid == os.getpid():
                    return True
                # Stale if owner dead OR lock older than TTL OR dir has no valid pid.
                if live and (age_s is None or age_s < _LOCK_TTL_S):
                    logger.warning("LOCK HELD by pid=%s age_s=%s (self=%s) — skip",
                                   old_pid, None if age_s is None else round(age_s, 1), os.getpid())
                    return False
            except Exception as e:
                logger.warning("LOCK inspect error: %s — treating as stale", e)
            # stale lock (owner dead, no pid, or TTL expired) -> force-remove
            if not _force_remove_lock():
                return False
            # re-loop: mkdir will now succeed (or race again)
            continue
        except (OSError, IOError) as e:
            logger.warning("LOCK mkdir OSError: %s", e)
            return False
    # If we exhausted retries WITHOUT creating the dir, a live holder owns it.
    if not acquired:
        logger.warning("LOCK not acquired after retries (dir still present) — skip")
        return False
    try:
        with open(os.path.join(_LOCK_DIR, "pid"), "w") as f:
            f.write(str(os.getpid()))
        # Touch an mtime marker so the TTL check has a reference point.
        with open(os.path.join(_LOCK_DIR, "mtime"), "w") as f:
            f.write(str(time.time()))
        return True
    except Exception as e:
        logger.warning("LOCK pid write failed: %s", e)
        return False


def _release_lock():
    try:
        pid_file = os.path.join(_LOCK_DIR, "pid")
        if os.path.exists(pid_file):
            with open(pid_file) as f:
                pid = f.read().strip()
            if pid == str(os.getpid()):
                import shutil
                shutil.rmtree(_LOCK_DIR, ignore_errors=True)
    except Exception:
        pass

# Ensure the dispatcher logs to a real file (run_job.py dispatch has no handler by
# default, which left dispatch logs empty and undebuggable).
_dh = logging.FileHandler(os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "logs", "dispatcher.log"), encoding="utf-8")
_dh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
if not logger.handlers:
    logger.addHandler(_h_disp := _dh)
logger.setLevel(logging.INFO)

GENERATE_RETRIES = 3
PUBLISH_RETRIES = 6
GEN_BACKOFF_S = 60
PUB_BACKOFF_S = 120
GEN_TIMEOUT_S = 3600   # 60 min backstop per render (CPU was hitting 40min; CUDA+NVENC now ~5-8min)
PUB_TIMEOUT_S = 600    # 10 min backstop per publish
MAX_REGEN = 3         # times a failed generate job is re-queued before giving up
MAX_REPUB = 3         # times a failed publish job is re-queued before giving up


def _recover_stuck(db, batch: str):
    """Reset posts that a previous dispatcher left mid-flight (crash/hang) so they
    can be re-drained. A post stuck in 'generating'/'publishing' with a dead or
    absent pid, or whose job is 'fired'/'missed'/'failed' (within retry budget)
    but the post never reached a terminal state, is reverted to a retryable state
    and its job re-queued. WITHOUT re-queuing the job, a recovered post would
    dead-end forever."""
    recovered = 0
    for p in db.query(ContentPost).filter(
            ContentPost.character_id == (batch.split(":")[0] if ":" in batch else ""),
            ContentPost.state.in_(["generating", "publishing"])).all():
        alive = False
        if p.pid:
            try:
                import psutil
                alive = psutil.Process(p.pid).is_running()
            except Exception:
                alive = False
        if not alive:
            # Revert to a retryable state.
            p.state = "scripted" if p.state == "generating" else "staged"
            p.pid = None
            p.heartbeat_at = None
            p.error_message = (p.error_message or "") + " | recovered by dispatcher (stuck)"
            for j in db.query(ScheduledJob).filter(
                    ScheduledJob.post_id == p.id,
                    ScheduledJob.status.in_(["fired", "pending", "missed", "failed"])).all():
                # Only re-queue if we haven't exhausted the self-heal budget.
                rc = getattr(j, "retry_count", 0) or 0
                if j.step == "generate" and rc <= MAX_REGEN:
                    j.status = "queued"
                    j.last_error = "recovered by dispatcher"
                elif j.step == "publish" and rc <= MAX_REPUB:
                    j.status = "queued"
                    j.last_error = "recovered by dispatcher"
                else:
                    # budget exhausted: leave failed so the planner surfaces it
                    j.status = "failed"
            recovered += 1
    if recovered:
        db.commit()
        logger.warning("[batch %s] recovered %d stuck post(s)", batch, recovered)
    return recovered


def _now():
    # Local time: all scheduled_time/fire_at values are naive-LOCAL (derived from
    # local midnight in the planner), so comparing against local now keeps the
    # "is this post due?" logic consistent. Using UTC here caused late-day posts
    # to appear perpetually future and never publish.
    return datetime.datetime.now()


def _repo_paths():
    repo = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    py = os.path.join(repo, ".venv", "Scripts", "python.exe")
    rj = os.path.join(repo, "run_job.py")
    return py, rj


def _run_job(argv_list: list, timeout_s: int) -> int:
    """Spawn run_job.py with the given args; return exit code."""
    py, rj = _repo_paths()
    repo = os.path.dirname(rj)
    full = [py, rj] + argv_list
    proc = subprocess.Popen(full, cwd=repo,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             creationflags=0x00000008)
    try:
        proc.wait(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        try:
            proc.kill()
        except Exception:
            pass
        return 124  # timeout
    return proc.returncode


def _arm_publish(post: ContentPost, db, planned_fire_at: datetime.datetime = None):
    """Arm the post's publish job at the planner's planned time (or +2min default)."""
    fire_at = planned_fire_at or (_now() + datetime.timedelta(minutes=2))
    job = db.query(ScheduledJob).filter(
        ScheduledJob.post_id == post.id,
        ScheduledJob.step == "publish",
        ScheduledJob.status.in_(["queued", "pending", "fired"]),
    ).first()
    if job:
        job.status = "queued"
        job.fire_at = fire_at
        job.last_error = None
    else:
        db.add(ScheduledJob(
            post_id=post.id, character_id=post.character_id, step="publish",
            fire_at=fire_at, status="queued",
            argv=json.dumps(["run_job.py", "publish", "--post-id", str(post.id),
                             "--character", post.character_id]),
            batch=post.character_id,
        ))
    db.commit()


def _drain_generates(db, batch: str, now: datetime.datetime) -> dict:
    done = 0
    failed = 0
    gen_jobs = db.query(ScheduledJob).filter(
        ScheduledJob.batch == batch,
        ScheduledJob.step == "generate",
        ScheduledJob.status == "queued",
    ).order_by(ScheduledJob.fire_at.asc()).all()
    for job in gen_jobs:
        post = db.query(ContentPost).get(job.post_id)
        if not post:
            job.status = "failed"; job.last_error = "post not found"; db.commit()
            continue
        if post.state != "scripted":
            # already rendered (idempotent) -> mark done, arm publish if staged
            job.status = "done"
            if post.state == "staged":
                _arm_publish(post, db)
            db.commit()
            continue
        logger.info("[batch %s] GENERATE post %s (attempt 1/%d)", batch, post.id, GENERATE_RETRIES)
        job.status = "fired"; job.last_error = f"pid=?"; db.commit()
        rc = _run_job(json.loads(job.argv)[1:], GEN_TIMEOUT_S)
        post = db.query(ContentPost).get(job.post_id)  # refresh
        if post.state == "staged":
            job.status = "done"
            _arm_publish(post, db)  # arm at planned time (scheduled_time)
            logger.info("[batch %s] post %s GENERATED -> staged, publish armed", batch, post.id)
            done += 1
        elif rc == 124:
            job.status = "failed"; job.last_error = "generate timeout"; post.state = "failed"
            db.commit(); failed += 1
        else:
            # retry with backoff
            ok = False
            for attempt in range(2, GENERATE_RETRIES + 1):
                logger.warning("[batch %s] post %s generate failed (rc=%s); retry %d/%d in %ss",
                               batch, post.id, rc, attempt, GENERATE_RETRIES, GEN_BACKOFF_S)
                time.sleep(GEN_BACKOFF_S)
                rc = _run_job(json.loads(job.argv)[1:], GEN_TIMEOUT_S)
                post = db.query(ContentPost).get(job.post_id)
                if post.state == "staged":
                    job.status = "done"; _arm_publish(post, db)
                    logger.info("[batch %s] post %s GENERATED on retry %d", batch, post.id, attempt)
                    ok = True; done += 1; break
            if not ok:
                # Self-heal: re-queue (don't dead-end) so a later dispatch retries,
                # up to MAX_REGEN times. Post stays 'scripted' (retryable).
                job.retry_count = (job.retry_count or 0) + 1
                if job.retry_count <= MAX_REGEN:
                    job.status = "queued"
                    job.last_error = f"generate exhausted attempts, re-queue {job.retry_count}/{MAX_REGEN}"
                    post.state = "scripted"; post.pid = None; post.heartbeat_at = None
                    post.error_message = (post.error_message or "") + f" | generate re-queued {job.retry_count}/{MAX_REGEN}"
                    logger.warning("[batch %s] post %s generate re-queued (attempt %d/%d)",
                                   batch, post.id, job.retry_count, MAX_REGEN)
                else:
                    job.status = "failed"; job.last_error = f"generate exhausted after {GENERATE_RETRIES} (+{MAX_REGEN} re-queues)"
                    post.state = "scripted"; post.pid = None; post.heartbeat_at = None
                    post.error_message = (post.error_message or "") + " | generate: given up"
                db.commit(); failed += 1
    return {"generated": done, "gen_failed": failed}


def _drain_publishes(db, batch: str, now: datetime.datetime) -> dict:
    done = 0
    failed = 0
    pub_jobs = db.query(ScheduledJob).filter(
        ScheduledJob.batch == batch,
        ScheduledJob.step == "publish",
        ScheduledJob.status == "queued",
        ScheduledJob.fire_at <= now,
    ).order_by(ScheduledJob.fire_at.asc()).all()
    for job in pub_jobs:
        post = db.query(ContentPost).get(job.post_id)
        if not post:
            job.status = "failed"; job.last_error = "post not found"; db.commit(); continue
        if post.state == "published":
            job.status = "done"; db.commit(); continue
        if post.state != "staged":
            # not ready yet (still generating) -> leave queued for a later dispatch
            continue
        logger.info("[batch %s] PUBLISH post %s", batch, post.id)
        job.status = "fired"; db.commit()
        rc = _run_job(json.loads(job.argv)[1:], PUB_TIMEOUT_S)
        post = db.query(ContentPost).get(job.post_id)
        if post.state == "published":
            job.status = "done"; logger.info("[batch %s] post %s PUBLISHED", batch, post.id); done += 1
        elif rc == 124:
            job.status = "failed"; job.last_error = "publish timeout"; post.state = "failed"; db.commit(); failed += 1
        elif rc == 3:
            # publish_job reports "not due yet" (scheduled_time in the future). This
            # is correct spacing, NOT a failure — re-queue the job (no retry/backoff
            # storm) so it fires again later when the planner's dispatch row is due.
            job.status = "queued"
            job.last_error = f"publish skipped: post {post.id} not due yet (scheduled_time in future)"
            logger.info("[batch %s] post %s publish not due yet — re-queued", batch, post.id)
            db.commit()
        else:
            ok = False
            for attempt in range(2, PUBLISH_RETRIES + 1):
                logger.warning("[batch %s] post %s publish failed (rc=%s); retry %d/%d in %ss",
                               batch, post.id, rc, attempt, PUBLISH_RETRIES, PUB_BACKOFF_S)
                time.sleep(PUB_BACKOFF_S)
                rc = _run_job(json.loads(job.argv)[1:], PUB_TIMEOUT_S)
                post = db.query(ContentPost).get(job.post_id)
                if post.state == "published":
                    job.status = "done"; logger.info("[batch %s] post %s PUBLISHED on retry %d", batch, post.id, attempt)
                    ok = True; done += 1; break
            if not ok:
                # Self-heal: re-queue publish (post stays 'staged') up to MAX_REPUB.
                job.retry_count = (job.retry_count or 0) + 1
                if job.retry_count <= MAX_REPUB:
                    job.status = "queued"
                    job.last_error = f"publish exhausted attempts, re-queue {job.retry_count}/{MAX_REPUB}"
                    post.state = "staged"
                    post.error_message = (post.error_message or "") + f" | publish re-queued {job.retry_count}/{MAX_REPUB}"
                    logger.warning("[batch %s] post %s publish re-queued (attempt %d/%d)",
                                   batch, post.id, job.retry_count, MAX_REPUB)
                else:
                    job.status = "failed"; job.last_error = f"publish exhausted after {PUBLISH_RETRIES} (+{MAX_REPUB} re-queues)"
                    post.state = "staged"; post.error_message = (post.error_message or "") + " | publish: given up"
                db.commit(); failed += 1
    return {"published": done, "pub_failed": failed}


def dispatch_batch(batch: str) -> dict:
    """Drain one batch: generates then publishes. Returns a summary dict.
    Robust: recovers stuck posts first, and isolates the generate/publish phases
    so a failure in one cannot block the other.

    Concurrency-guarded: if another dispatcher is already draining (the planner
    can fire multiple per-slot dispatch rows while a long render is still
    running), this invocation exits immediately instead of colliding on the
    same jobs/posts."""
    if not _acquire_lock():
        logger.warning("=== DISPATCH batch=%s SKIPPED: another dispatcher holds the lock ===", batch)
        return {"skipped": True, "reason": "locked"}
    try:
        db = SessionLocal()
        now = _now()
        try:
            logger.info("=== DISPATCH batch=%s start ===", batch)
            _recover_stuck(db, batch)
            try:
                gen = _drain_generates(db, batch, now)
            except Exception as e:
                logger.exception("[batch %s] generate phase crashed: %s", batch, e)
                gen = {"generated": 0, "gen_failed": 0, "gen_error": str(e)}
            # Publish phase ALWAYS runs (even if generate had issues) — staged posts
            # from prior or partial runs must still go live.
            try:
                pub = _drain_publishes(db, batch, now)
            except Exception as e:
                logger.exception("[batch %s] publish phase crashed: %s", batch, e)
                pub = {"published": 0, "pub_failed": 0, "pub_error": str(e)}
            summary = {**gen, **pub}
            logger.info("=== DISPATCH batch=%s done: %s ===", batch, summary)
            return summary
        finally:
            db.close()
    finally:
        _release_lock()


def dispatch_due() -> dict:
    """Drain every batch that has a 'dispatch' row due now (planner invoked)."""
    if not _acquire_lock():
        logger.warning("=== DISPATCH_DUE SKIPPED: another dispatcher holds the lock ===")
        return {"skipped": True, "reason": "locked"}
    try:
        db = SessionLocal()
        now = _now()
        try:
            due = db.query(ScheduledJob).filter(
                ScheduledJob.step == "dispatch",
                ScheduledJob.status == "queued",
                ScheduledJob.fire_at <= now,
            ).order_by(ScheduledJob.fire_at.asc()).all()
            combined = {}
            for d in due:
                d.status = "fired"; db.commit()
                res = dispatch_batch(d.batch or "")
                for k, v in res.items():
                    # dispatch_batch may carry string error keys (gen_error/pub_error)
                    # when a phase crashed; those must not be summed with ints.
                    if isinstance(v, (int, float)) and isinstance(combined.get(k, 0), (int, float)):
                        combined[k] = combined.get(k, 0) + v
                    else:
                        combined[k] = v
                d.status = "done"; db.commit()
            return combined
        finally:
            db.close()
    finally:
        _release_lock()


# ----------------------------------------------------------------------------
# Backward-compatible helpers (used by generate_job / publish_job / plan_job).
# The new planner uses dispatch_batch / dispatch_due directly.
# ----------------------------------------------------------------------------
def build_generate_argv(post: ContentPost) -> list:
    return ["run_job.py", "generate", "--post-id", str(post.id), "--character", post.character_id]


def build_publish_argv(post: ContentPost) -> list:
    return ["run_job.py", "publish", "--post-id", str(post.id), "--character", post.character_id]


def schedule_publish(post: ContentPost, delay_min: int = 2) -> None:
    """Arm the publish job for a post that just finished rendering.

    Honors the planner's planned time (post.scheduled_time) when set, else +2min.
    Kept for generate_job compatibility; the dispatcher's _arm_publish is the
    canonical arming path.
    """
    planned = post.scheduled_time if (hasattr(post, "scheduled_time") and post.scheduled_time) else None
    fire_at = planned or (_now() + datetime.timedelta(minutes=delay_min))
    db = SessionLocal()
    try:
        _arm_publish(post, db, planned_fire_at=fire_at)
    finally:
        db.close()


def mark_done(post_id: int, step: str, ok: bool, error: str = None):
    """Close the ledger row for a post/step (called by publish_job)."""
    db = SessionLocal()
    try:
        j = db.query(ScheduledJob).filter(
            ScheduledJob.post_id == post_id,
            ScheduledJob.step == step,
            ScheduledJob.status.in_(["queued", "pending", "fired"]),
        ).order_by(ScheduledJob.fire_at.desc()).first()
        if j:
            j.status = "done" if ok else "failed"
            if error:
                j.last_error = error
            db.commit()
    finally:
        db.close()


def fire_due(now: datetime.datetime = None, dry_run: bool = False) -> dict:
    """Legacy single-unit fire path (kept for compatibility; the planner now
    primarily uses dispatch_due). Fires at most one due job."""
    now = now or _now()
    db = SessionLocal()
    summary = {"fired": 0, "done": 0, "skipped": 0, "blocked": 0, "dry": dry_run}
    try:
        due = db.query(ScheduledJob).filter(
            ScheduledJob.status == "pending",
            ScheduledJob.fire_at <= now,
        ).order_by(ScheduledJob.fire_at.asc()).all()
        for job in due:
            argv = json.loads(job.argv)
            post = db.query(ContentPost).get(job.post_id)
            if not post:
                job.status = "failed"; job.last_error = "post not found"; db.commit(); continue
            if job.step == "generate" and post.state != "scripted":
                job.status = "done"; db.commit(); continue
            if job.step == "publish" and post.state != "staged":
                if post.state in ("scripted", "generating"):
                    job.status = "pending"
                    job.fire_at = now + datetime.timedelta(minutes=15)
                    db.commit(); continue
                job.status = "done"; db.commit(); continue
            if dry_run:
                summary["skipped"] += 1; continue
            py, rj = _repo_paths()
            proc = subprocess.Popen([py, rj] + argv[1:], cwd=os.path.dirname(rj),
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                    creationflags=0x00000008)
            job.status = "fired"; job.last_error = f"pid={proc.pid}"; db.commit()
            summary["fired"] += 1; break
        return summary
    finally:
        db.close()


class DispatchJob:
    """run_job.py `dispatch` entrypoint — drains a batch (or all due batches)."""
    name = "dispatch"

    def execute(self, args: dict) -> int:
        batch = args.get("batch")
        if batch:
            summary = dispatch_batch(batch)
        else:
            summary = dispatch_due()
        logger.info("dispatch result: %s", summary)
        return 0
