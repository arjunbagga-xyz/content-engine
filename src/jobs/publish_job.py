"""Publish job — publishes EXACTLY ONE post, specified by --post-id.

NOT a queue blaster. The planner fires this with the specific post-id when that
post's scheduled_time has arrived. Brings the tunnel up for the duration, tears it
down after (no lingering cloudflared/origin server). Records heartbeat so the
planner can detect a crashed publish and recover it.

States: staged -> publishing (heartbeat) -> published | failed.
"""
import os
import datetime
from sqlalchemy.orm import Session

from src.jobs.base import JobBase
from src.memory.db import SessionLocal, ContentPost
from src.publishing import tunnel as tunnel_mgr
from src.publishing.publisher import PublisherRouter


class PublishJob(JobBase):
    name = "publish"

    async def run(self, args: dict = None) -> int:
        args = args or {}
        post_id = args.get("post_id")

        db: Session = SessionLocal()
        try:
            if post_id is None:
                self.logger.error("publish requires --post-id (specific, not queue-blaster)")
                return 2
            post = db.query(ContentPost).get(int(post_id))
            if post is None:
                self.logger.error("[post %s] not found", post_id)
                return 2
            if post.state != "staged":
                # Still rendering (generating) or otherwise not ready — signal the
                # dispatcher to retry later rather than treating this as "done".
                # A slow render must not strand a staged post unpublished.
                self.logger.info("[post %s] state=%s (not staged yet) — not ready, will retry",
                                 post_id, post.state)
                return 3
            if post.scheduled_time and post.scheduled_time > datetime.datetime.now():
                self.logger.info("[post %s] not due yet (scheduled=%s) — skipping",
                                 post_id, post.scheduled_time)
                # Distinct code (NOT 0): 0 means "ran fine" and would make the
                # dispatcher think the post failed to publish and retry it. 3 means
                # "correctly skipped, not due" — the dispatcher re-queues without
                # wasting retries. (Same code as the not-staged-yet path above.)
                return 3

            # Heartbeat: mark publishing + record pid.
            post.state = "publishing"
            post.pid = os.getpid()
            post.heartbeat_at = datetime.datetime.utcnow()
            db.commit()
            self.logger.info("[post %s] publishing (pid=%s)", post_id, post.pid)

            try:
                PublisherRouter.publish_post(db, post)
                post.state = "published"
                post.actual_posted_time = datetime.datetime.utcnow()
                self.logger.info("[post %s] PUBLISHED -> %s", post.id, post.platform_post_id)
                from src.jobs.dispatcher import mark_done
                mark_done(post.id, "publish", ok=True)
                return 0
            except Exception as e:
                self.logger.error("[post %s] publish failed: %s", post.id, e)
                post.error_message = f"publish: {e}"
                post.retry_count = (post.retry_count or 0) + 1
                # Close the ledger row so it can be re-queued: if we still have
                # retries left, set it back to 'pending' +10min so the next planner
                # run retries; otherwise mark failed. Without this the row stays
                # 'fired' forever and the post never retries.
                from src.jobs.dispatcher import mark_done, _requeue_publish
                if post.retry_count < 6:
                    _requeue_publish(post.id, delay_min=10)
                else:
                    mark_done(post.id, "publish", ok=False, error=str(e))
                    post.state = "failed"
                return 1
            finally:
                post.pid = None
                post.heartbeat_at = None
                db.commit()
                try:
                    tunnel_mgr.stop_tunnel()
                except Exception as e:
                    self.logger.warning("stop_tunnel warning: %s", e)
        finally:
            db.close()


if __name__ == "__main__":
    import sys
    sys.exit(PublishJob().execute())
