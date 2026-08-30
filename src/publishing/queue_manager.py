import logging
import datetime
import random
import time
from sqlalchemy.orm import Session
from src.memory.db import ContentPost, Character
from src.publishing.publisher import PublisherRouter
from src.core.config import config

logger = logging.getLogger("content_engine.queue_manager")

class ContentQueueManager:
    def __init__(self, db: Session, dry_run: bool = False):
        self.db = db
        self.dry_run = dry_run

    def stage_post(self, post_id: int) -> bool:
        """Transitions a post from 'media_ready' to 'staged' after QA approval."""
        post = self.db.query(ContentPost).filter(ContentPost.id == post_id).first()
        if not post:
            logger.error(f"Post {post_id} not found in database.")
            return False

        if post.state != "media_ready":
            logger.warning(f"Post {post_id} is in state '{post.state}', expected 'media_ready' to stage.")
            return False

        post.state = "staged"
        self.db.commit()
        logger.info(f"Post {post_id} successfully staged for publishing.")
        return True

    def process_publishing_queue(self) -> int:
        """
        Pulls all staged posts scheduled for the past, publishes them,
        and handles retry schedules for failures.
        """
        now = datetime.datetime.utcnow()
        # Only publish posts for accounts that are still active. Posts left
        # queued for deactivated accounts (e.g. dev/artifact accounts) would
        # otherwise retry forever and waste CPU — skip them here.
        active_ids = {
            c.id for c in self.db.query(Character).filter(Character.status == "active").all()
        }
        # Find posts that are staged and scheduled in the past/present
        due_posts = (
            self.db.query(ContentPost)
            .filter(ContentPost.state == "staged")
            .filter(ContentPost.scheduled_time <= now)
            .order_by(ContentPost.scheduled_time.asc())
            .all()
        )
        due_posts = [p for p in due_posts if p.character_id in active_ids]
        if active_ids:
            logger.info(f"Active accounts for publishing: {sorted(active_ids)}")

        if not due_posts:
            logger.info("No content due for publishing at this time.")
            return 0

        logger.info(f"Found {len(due_posts)} posts due for publishing.")
        published_count = 0

        # Safety constraints check: Max posts per IG account per day
        for post in due_posts:
            char_id = post.character_id
            platform = post.platform.lower()

            # Check daily soft limits (e.g. 10 posts per day per IG account)
            if platform == "instagram":
                today_start = datetime.datetime.combine(datetime.date.today(), datetime.time.min)
                today_published = (
                    self.db.query(ContentPost)
                    .filter(ContentPost.character_id == char_id)
                    .filter(ContentPost.platform == "instagram")
                    .filter(ContentPost.state == "published")
                    .filter(ContentPost.actual_posted_time >= today_start)
                    .count()
                )
                if today_published >= 10:
                    logger.warning(f"Instagram safety limit reached for '{char_id}'. Post {post.id} held.")
                    post.state = "held"
                    post.error_message = "Daily safety limit of 10 posts reached. Post held."
                    self.db.commit()
                    continue

            # Process the publish
            try:
                # Route through Publisher
                PublisherRouter.publish_post(self.db, post, dry_run=self.dry_run)
                published_count += 1
                
                # To prevent bot detection, let's inject a human delay between different account posts
                if len(due_posts) > 1 and post != due_posts[-1]:
                    extra_delay = random.randint(15, 60)
                    logger.info(f"Staggering separate account uploads: Sleeping for {extra_delay}s...")
                    if not self.dry_run:
                        time.sleep(extra_delay)
            except Exception as e:
                logger.error(f"Failed to publish post {post.id}: {str(e)}")
                
                # State was updated in PublisherRouter (retry count incremented, state set to scripted/failed)
                # Let's adjust scheduled time for a retry with exponential backoff:
                # Retry 1: +5 min, Retry 2: +15 min, Retry 3: +45 min
                retry_delays = [5, 15, 45]
                idx = min(post.retry_count - 1, len(retry_delays) - 1)
                backoff_minutes = retry_delays[idx]
                
                # Reschedule post
                new_schedule = datetime.datetime.utcnow() + datetime.timedelta(minutes=backoff_minutes)
                post.scheduled_time = new_schedule
                
                # If still active (state == scripted), restage it so it gets processed again next run
                if post.state == "scripted":
                    post.state = "staged"
                    logger.info(f"Post {post.id} rescheduled for retry in {backoff_minutes} mins at {new_schedule}")
                else:
                    logger.error(f"Post {post.id} has exceeded maximum retries and is marked as permanently failed.")
                    
                self.db.commit()

        logger.info(f"Completed queue processing. Successfully published {published_count} posts.")
        return published_count
