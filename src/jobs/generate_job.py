"""Generate job — renders EXACTLY ONE post, specified by --post-id.

NOT a queue blaster. The planner (6x/day) decides what to generate and when, then
fires this with the specific post-id + account + pipeline. This process renders one
reel, records its heartbeat (pid) so the planner can detect crashes, then exits.

States: scripted -> generating (heartbeat set) -> staged | failed.
"""
import os
import asyncio
import subprocess
from pathlib import Path
from sqlalchemy.orm import Session

from src.jobs.base import JobBase
from src.core.config import config, OUTPUTS_DIR
from src.memory.db import SessionLocal, ContentPost
from src.generation import pipelines as PL
from src.generation import sprite_reactor as SR


class GenerateJob(JobBase):
    name = "generate"
    hard_timeout_s = 2400  # 40 min backstop per single reel

    async def run(self, args: dict = None) -> int:
        args = args or {}
        post_id = args.get("post_id")
        character = args.get("character")
        pipeline_arg = args.get("pipeline")
        topic = args.get("topic")

        db: Session = SessionLocal()
        try:
            if post_id is None:
                self.logger.error("generate requires --post-id (specific, not queue-blaster)")
                return 2
            post = db.query(ContentPost).get(int(post_id))
            if post is None:
                self.logger.error("[post %s] not found", post_id)
                return 2
            if post.state != "scripted":
                self.logger.info("[post %s] state=%s (not scripted) — nothing to do", post_id, post.state)
                return 0

            # Heartbeat: mark generating + record pid so planner detects crashes.
            post.state = "generating"
            post.pid = os.getpid()
            post.heartbeat_at = _now()
            db.commit()
            self.logger.info("[post %s] generating (pid=%s)", post_id, post.pid)

            ok = await self._generate_one(db, post, character, pipeline_arg, topic)
            return 0 if ok else 1
        finally:
            db.close()

    async def _generate_one(self, db, post, character, pipeline_arg, topic) -> bool:
        char_id = post.character_id
        out = str(OUTPUTS_DIR / f"post_{post.id}_{char_id}_reel.mp4")
        try:
            acct = SR.SpriteReactor._get_account(char_id) or {}
            pn = pipeline_arg or PL.select_pipeline(acct)
            prod = PL.get_producer(pn)
            self.logger.info("[post %s] pipeline=%s topic=%s", post.id, pn, topic)
            # Per-turn tone plan resolved by the planner (stored on the post).
            tone_plan = None
            if post.tone_plan:
                try:
                    tone_plan = json.loads(post.tone_plan)
                except Exception:
                    tone_plan = None
            r = await asyncio.wait_for(
                prod(char_id, out, topic=topic, num_turns=None, tone_plan=tone_plan),
                timeout=self.hard_timeout_s,
            )
            path = r.get("path")
            if not (path and os.path.exists(path)):
                raise RuntimeError("producer returned no file")
            if not self._is_valid_video(path):
                raise RuntimeError("produced file failed ffprobe validation")
            post.media_path = path
            post.media_type = "video"
            post.state = "staged"
            self.logger.info("[post %s] GENERATED -> %s (staged)", post.id, os.path.basename(path))
            # Arm the publish job ~2 min after render completes (decoupled from a
            # fixed clock, so slow renders still get published).
            from src.jobs.dispatcher import schedule_publish
            schedule_publish(post, delay_min=2)
            return True
        except Exception as e:
            self.logger.error("[post %s] generation failed: %s", post.id, e)
            post.state = "failed"
            post.error_message = f"generate: {e}"
            post.retry_count = (post.retry_count or 0) + 1
            return False
        finally:
            # Clear heartbeat regardless of outcome.
            post.pid = None
            post.heartbeat_at = None
            db.commit()

    @staticmethod
    def _is_valid_video(path: str) -> bool:
        try:
            r = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "stream=codec_type",
                 "-of", "csv=p=0", path],
                capture_output=True, text=True, timeout=30,
            )
            return r.returncode == 0 and "video" in r.stdout
        except Exception:
            return False


def _now():
    import datetime
    return datetime.datetime.utcnow()


if __name__ == "__main__":
    import sys
    sys.exit(GenerateJob().execute())
