"""Plan job — the ONLY scheduler. Runs 6x/day. The brain.

For each active account it:
  1. RECONCILES: recovers crashed posts (heartbeat), reclaims 'missed' ScheduledJob
     rows (dispatcher died mid-batch), and surfaces 'failed' posts for audit.
  2. RESOLVES content (at PLAN TIME, logged & inspectable):
       - subtopic LLM: broad theme -> concrete, specific, timely topic (no bleed)
       - tone plan LLM: per-character per-turn emotion sequence (tone shifts in debate)
     Both are stored on the ContentPost (topic, tone_plan) so generation is
     deterministic and the planner's decisions are auditable.
  3. BUILDS the day's batch: posts_per_day (PER ACCOUNT, from characters.yaml)
     slots spread across 24h (>=30min apart). For each slot: one ContentPost
     (scripted) + one 'generate' ScheduledJob (queued) + one 'publish' ScheduledJob
     (queued, fire_at spread through the day). Plus ONE 'dispatch' ScheduledJob that
     fires the batch drainer at the first generate slot.
  4. The planner does NOT run jobs itself — it only writes ledger rows. The dispatcher
     (run_job.py dispatch --batch KEY) drains the batch when the dispatch row is due.
     If a dispatch row is already due now, the planner kicks it once for immediacy.

Idempotent: re-runs are no-ops (coverage + ScheduledJob existence checks per batch).
"""
import json
import datetime

from sqlalchemy.orm import Session

from src.jobs.base import JobBase, load_settings
from src.memory.db import SessionLocal, ContentPost, Character, ScheduledJob
from src.generation import sprite_reactor as SR
from src.generation import subtopic as SUB
from src.jobs import dispatcher

MIN_GAP_MIN = 30          # minimum minutes between any two posts of one account
GEN_PUB_BUFFER_MIN = 10   # default publish delay after a generate slot (planner sets per post)


def _now():
    # Local time, consistent with naive-local scheduled_time/fire_at written by the
    # planner (derived from local midnight). UTC here broke the "due?" comparison.
    return datetime.datetime.now()


def _pid_alive(pid):
    if not pid:
        return False
    try:
        import psutil
        return psutil.Process(pid).is_running()
    except Exception:
        return False


class PlanJob(JobBase):
    name = "plan"

    async def run(self, args: dict = None) -> int:
        db: Session = SessionLocal()
        try:
            active = db.query(Character).filter(Character.status == "active").all()
            self.logger.info("Planner running for %d active account(s)", len(active))
            for char in active:
                await self._plan_account(db, char.id)
            # Kick any dispatch row already due now (immediacy; normal trigger is the
            # scheduled dispatch row firing via run_job.py dispatch --batch KEY).
            kicked = dispatcher.dispatch_due()
            self.logger.info("Dispatcher kicked due: %s", kicked)
            self.logger.info("Planner complete.")
            return 0
        finally:
            db.close()

    # ---------------------------------------------------------------- account
    async def _plan_account(self, db: Session, char_id: str):
        acct_conf = SR.SpriteReactor._get_account(char_id) or {}
        is_faceless = (acct_conf.get("type") or "").lower() == "faceless"
        pipelines = acct_conf.get("pipeline") or acct_conf.get("pipelines") or []
        if isinstance(pipelines, str):
            pipelines = [pipelines]
        is_faceless = is_faceless or any("debate" in str(p).lower() for p in pipelines)
        # PER-ACCOUNT posts_per_day (single source of truth: characters.yaml).
        posts_per_day = int(acct_conf.get("posts_per_day", 1))

        # 1. Reconcile crashes / missed / failed.
        self._reconcile(db, char_id)

        # 1b. Migrate legacy ledger rows (batch=None, status='pending') into the new
        # batch model so they are not orphaned by the dispatcher (which only fires
        # 'dispatch' rows, not bare 'pending' generate jobs). Adopt them into today's
        # batch and flip to 'queued'; ensure a dispatch row exists to drain them.
        today = _now().date()
        batch = f"{char_id}:{today.isoformat()}"
        legacy = db.query(ScheduledJob).filter(
            ScheduledJob.character_id == char_id,
            ScheduledJob.batch.is_(None),
            ScheduledJob.status == "pending",
        ).all()
        for j in legacy:
            post = db.query(ContentPost).get(j.post_id)
            if post and post.state in ("staged", "published", "failed"):
                j.status = "done"   # already handled; close the orphan
            else:
                j.batch = batch
                j.status = "queued"
        if legacy:
            # ensure a dispatch row exists for the batch so it gets drained
            has_disp = db.query(ScheduledJob).filter(
                ScheduledJob.batch == batch, ScheduledJob.step == "dispatch").first()
            if not has_disp and legacy:
                # point dispatch at the earliest remaining job's post
                first = legacy[0]
                db.add(ScheduledJob(post_id=first.post_id, character_id=char_id,
                                   step="dispatch", fire_at=_now(),
                                   argv=json.dumps(["run_job.py", "dispatch", "--batch", batch]),
                                   status="queued", batch=batch))
            db.commit()

        # 2. Resolve content + build batch if today's batch is incomplete.
        today = _now().date()
        today_start = datetime.datetime(today.year, today.month, today.day)
        batch = f"{char_id}:{today.isoformat()}"

        existing_today = db.query(ContentPost).filter(
            ContentPost.character_id == char_id,
            ContentPost.scheduled_time >= today_start,
        ).count()
        if existing_today >= posts_per_day:
            self.logger.info("[%s] batch %s already has %d posts (>=%d); skipping",
                             char_id, batch, existing_today, posts_per_day)
            return

        # Wide themes for this account (broad universe -> subtopic LLM narrows it).
        themes = acct_conf.get("themes", []) or acct_conf.get("topics", []) or ["general"]
        if isinstance(themes, str):
            themes = [themes]
        role = acct_conf.get("role", char_id)

        slots = self._spread_slots(posts_per_day)
        # Publish times spread through the day (independent of generate burst).
        pub_slots = self._spread_slots(posts_per_day, start_hour=9, end_hour=21)

        created = 0
        for i, slot in enumerate(slots):
            if self._slot_covered(db, char_id, slot):
                continue
            # Resolve ONE concrete subtopic for this post (LLM, logged).
            theme = themes[i % len(themes)] if themes else "general"
            topic = await SUB.resolve_subtopic(theme, role, char_id)
            # Resolve per-turn tone plan for the account's characters.
            chars = list((acct_conf.get("characters") or {}).values())
            tone_plan = await SUB.resolve_tone_plan(chars, topic, turns=6) if chars else {}
            created += self._create_unit(
                db, char_id, batch, slot, pub_slots[i % len(pub_slots)],
                topic, tone_plan, is_faceless,
            )
        self.logger.info("[%s] batch %s posts_per_day=%d -> created %d unit(s)",
                         char_id, batch, posts_per_day, created)

    # ---------------------------------------------------------------- reconcile
    def _reconcile(self, db: Session, char_id: str):
        # Recover crashed posts (generating/publishing with dead heartbeat).
        stuck = db.query(ContentPost).filter(
            ContentPost.character_id == char_id,
            ContentPost.state.in_(["generating", "publishing"]),
        ).all()
        for p in stuck:
            alive = _pid_alive(p.pid)
            stale = (p.heartbeat_at is not None and
                     (_now() - p.heartbeat_at).total_seconds() > 3 * 3600)
            if not alive or stale:
                p.state = "scripted" if p.state == "generating" else "staged"
                p.pid = None
                p.heartbeat_at = None
                p.error_message = (p.error_message or "") + " | recovered by planner (crash/heartbeat)"
                for j in db.query(ScheduledJob).filter(
                        ScheduledJob.post_id == p.id,
                        ScheduledJob.status.in_(["pending", "queued", "fired"])).all():
                    j.status = "missed"
                self.logger.warning("[%s] recovered crashed post %s", char_id, p.id)
        # Reclaim 'missed' ScheduledJob rows -> re-queue so dispatcher retries.
        missed = db.query(ScheduledJob).filter(
            ScheduledJob.character_id == char_id,
            ScheduledJob.status == "missed",
        ).all()
        for j in missed:
            j.status = "queued"
            j.last_error = "reclaimed by planner"
        # Surface 'failed' posts (do not silently stall).
        failed = db.query(ContentPost).filter(
            ContentPost.character_id == char_id,
            ContentPost.state == "failed",
        ).all()
        for p in failed:
            self.logger.error("[%s] FAILED post %s: %s", char_id, p.id, p.error_message)
        if missed or failed:
            db.commit()

    # ---------------------------------------------------------------- scheduling
    def _spread_slots(self, n, start_hour=0, end_hour=24):
        """STABLE fixed grid: n points across [start_hour, end_hour), anchored to
        local midnight. Identical on every run -> idempotent."""
        now = _now()
        midnight = datetime.datetime(now.year, now.month, now.day)
        span = (end_hour - start_hour) * 60
        out = []
        for i in range(n):
            frac = (i + 0.5) / n
            mins = int(start_hour * 60 + frac * span)
            out.append(midnight + datetime.timedelta(minutes=mins))
        return out

    def _slot_covered(self, db: Session, char_id: str, slot):
        tol = max(MIN_GAP_MIN, int(24 * 60 / max(1, self._last_n)) // 2) if hasattr(self, "_last_n") else MIN_GAP_MIN
        lo = slot - datetime.timedelta(minutes=tol)
        hi = slot + datetime.timedelta(minutes=tol)
        today = _now().date()
        today_start = datetime.datetime(today.year, today.month, today.day)
        return db.query(ContentPost).filter(
            ContentPost.character_id == char_id,
            ContentPost.scheduled_time >= today_start,
            ContentPost.scheduled_time >= lo,
            ContentPost.scheduled_time <= hi,
        ).first() is not None

    # ---------------------------------------------------------------- create unit
    def _create_unit(self, db, char_id, batch, slot, pub_slot, topic, tone_plan,
                     is_faceless):
        post = ContentPost(
            character_id=char_id,
            platform="instagram",
            post_type="debate" if is_faceless else "reel",
            state="scripted",
            scheduled_time=slot,
            topic=topic,
            tone_plan=json.dumps(tone_plan) if tone_plan else None,
        )
        db.add(post)
        db.flush()

        gen_argv = dispatcher.build_generate_argv(post)
        pub_argv = dispatcher.build_publish_argv(post)

        # Generate job: queued, consumed by the dispatcher drainer.
        db.add(ScheduledJob(post_id=post.id, character_id=char_id, step="generate",
                           fire_at=slot, argv=json.dumps(gen_argv),
                           status="queued", batch=batch))
        # Publish job: queued, fire_at spread through the day (planner controls timing).
        db.add(ScheduledJob(post_id=post.id, character_id=char_id, step="publish",
                           fire_at=pub_slot, argv=json.dumps(pub_argv),
                           status="queued", batch=batch))
        # Dispatch rows: one per meaningful drain trigger — at the generate slot AND at
        # each publish slot. This gives the planner FULL control over when the dispatcher
        # runs (generates in a burst, then publishes drip out at the planned times). Each
        # dispatch drains whatever is due in the batch at that moment.
        # Idempotent: only add a dispatch row if none already covers that exact fire_at.
        for trig in (slot, pub_slot):
            exists = db.query(ScheduledJob).filter(
                ScheduledJob.batch == batch, ScheduledJob.step == "dispatch",
                ScheduledJob.fire_at == trig).first()
            if not exists:
                db.add(ScheduledJob(post_id=post.id, character_id=char_id, step="dispatch",
                                   fire_at=trig, argv=json.dumps(["run_job.py", "dispatch",
                                   "--batch", batch]), status="queued", batch=batch))
        db.commit()
        self.logger.info("[%s] scheduled post %d: topic='%s' gen@%s pub@%s batch=%s",
                         char_id, post.id, topic[:60], slot.strftime("%H:%M"),
                         pub_slot.strftime("%H:%M"), batch)
        return 1


if __name__ == "__main__":
    import sys
    sys.exit(PlanJob().execute())
