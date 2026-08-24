import os
import time
import random
import logging
import requests
from pathlib import Path
from src.core.config import config

logger = logging.getLogger("content_engine.publisher")

# Optional imports with graceful fallbacks for testing
try:
    from instagrapi import Client
    from instagrapi.exceptions import ChallengeRequired, LoginRequired
    INSTAGRAPI_AVAILABLE = True
except ImportError:
    INSTAGRAPI_AVAILABLE = False
    logger.warning("instagrapi package is not installed. Instagram operations will run in simulation mode.")

try:
    import tweepy
    TWEEPY_AVAILABLE = True
except ImportError:
    TWEEPY_AVAILABLE = False
    logger.warning("tweepy package is not installed. X operations will run in simulation mode.")

class InstagramPublisher:
    def __init__(self, character_id: str, dry_run: bool = False):
        self.character_id = character_id
        self.dry_run = dry_run
        self.creds = config.get_social_credentials(character_id, "instagram")
        self.username = self.creds.get("username")
        self.password = self.creds.get("password")
        self.session_file = config.SESSIONS_DIR / f"{character_id}_ig_session.json"
        self.client = None

    def _init_client(self):
        if not INSTAGRAPI_AVAILABLE:
            raise RuntimeError("instagrapi not installed.")
            
        if not self.username or not self.password or self.username == "your_username_here":
            raise ValueError("Instagram credentials not configured in .env")

        cl = Client()
        cl.delay_range = [2, 5] # Anti-bot measure: built-in random delay between private API requests
        
        # Try loading cached session
        if self.session_file.exists():
            try:
                logger.info(f"Loading cached Instagram session for {self.character_id}...")
                cl.load_settings(self.session_file)
                # Verify session is still valid
                cl.get_timeline_feed()
                self.client = cl
                logger.info("Instagram session restored successfully.")
                return
            except Exception as e:
                logger.warning(f"Cached session invalid: {str(e)}. Re-authenticating...")
                
        # Fresh login
        logger.info(f"Attempting fresh Instagram login for {self.username}...")
        try:
            cl.login(self.username, self.password)
            cl.dump_settings(self.session_file)
            self.client = cl
            logger.info("Successfully logged in and saved session.")
        except Exception as e:
            if "Challenge" in str(type(e)) or "Challenge" in str(e):
                logger.error("Instagram login challenge required! Manual intervention needed.")
                self._send_alert(f"⚠️ Instagram Login Challenge required for @{self.username}!")
            raise e

    def _send_alert(self, message: str):
        """Sends alert message to configured Webhook (e.g. Discord/Slack)."""
        if config.WEBHOOK_URL and config.WEBHOOK_URL != "your_webhook_url_here":
            try:
                requests.post(config.WEBHOOK_URL, json={"content": message}, timeout=5)
            except Exception as e:
                logger.error(f"Failed to send webhook alert: {str(e)}")

    def _random_delay(self):
        """Sleeps for a random duration to emulate human typing/posting."""
        delay = random.randint(30, 120)
        logger.info(f"Emulating human behavior: Sleeping for {delay} seconds before posting...")
        if not self.dry_run:
            time.sleep(delay)

    def publish_photo(self, image_path: str, caption: str) -> str:
        """Publishes a static photo to Instagram Feed."""
        logger.info(f"Publishing photo to Instagram: {image_path}")
        if self.dry_run or not self.username or self.username == "your_username_here":
            logger.info(f"[SIMULATION] Mock upload photo: {image_path} with caption: {caption[:30]}...")
            return f"mock_ig_photo_{int(time.time())}"
            
        self._random_delay()
        if not self.client:
            self._init_client()
            
        try:
            path = Path(image_path)
            media = self.client.photo_upload(path, caption)
            logger.info(f"Photo successfully posted! IG Media ID: {media.pk}")
            return str(media.pk)
        except Exception as e:
            logger.error(f"Failed to upload photo to IG: {str(e)}")
            raise e

    def publish_reel(self, video_path: str, caption: str) -> str:
        """Publishes a video clip as an Instagram Reel."""
        logger.info(f"Publishing Reel to Instagram: {video_path}")
        if self.dry_run or not self.username or self.username == "your_username_here":
            logger.info(f"[SIMULATION] Mock upload Reel: {video_path} with caption: {caption[:30]}...")
            return f"mock_ig_reel_{int(time.time())}"
            
        self._random_delay()
        if not self.client:
            self._init_client()
            
        try:
            path = Path(video_path)
            # Instagrapi supports thumbnail generation automatically, or we can pass a thumbnail path
            media = self.client.clip_upload(path, caption)
            logger.info(f"Reel successfully posted! IG Media ID: {media.pk}")
            return str(media.pk)
        except Exception as e:
            logger.error(f"Failed to upload Reel to IG: {str(e)}")
            raise e

    def publish_carousel(self, image_paths: list, caption: str) -> str:
        """Publishes multiple photos as a single IG Carousel post."""
        logger.info(f"Publishing Carousel of {len(image_paths)} images to Instagram...")
        if self.dry_run or not self.username or self.username == "your_username_here":
            logger.info(f"[SIMULATION] Mock upload Carousel of {len(image_paths)} files.")
            return f"mock_ig_carousel_{int(time.time())}"
            
        self._random_delay()
        if not self.client:
            self._init_client()
            
        try:
            paths = [Path(p) for p in image_paths]
            media = self.client.album_upload(paths, caption)
            logger.info(f"Carousel successfully posted! IG Media ID: {media.pk}")
            return str(media.pk)
        except Exception as e:
            logger.error(f"Failed to upload Carousel: {str(e)}")
            raise e

class XPublisher:
    def __init__(self, character_id: str, dry_run: bool = False):
        self.character_id = character_id
        self.dry_run = dry_run
        self.creds = config.get_social_credentials(character_id, "x")
        self.client_v2 = None
        self.api_v1 = None

    def _init_client(self):
        if not TWEEPY_AVAILABLE:
            raise RuntimeError("tweepy not installed.")
            
        c_key = self.creds.get("consumer_key")
        c_sec = self.creds.get("consumer_secret")
        a_tok = self.creds.get("access_token")
        a_sec = self.creds.get("access_token_secret")
        b_tok = self.creds.get("bearer_token")
        
        if not c_key or c_key == "your_key_here":
            raise ValueError(f"X API credentials not configured in .env for {self.character_id}")

        # V2 client is used for posting text and managing threads
        self.client_v2 = tweepy.Client(
            bearer_token=b_tok,
            consumer_key=c_key,
            consumer_secret=c_sec,
            access_token=a_tok,
            access_token_secret=a_sec
        )
        
        # V1.1 API is required for uploading media (images/videos)
        auth = tweepy.OAuth1UserHandler(c_key, c_sec, a_tok, a_sec)
        self.api_v1 = tweepy.API(auth)
        logger.info(f"X Tweepy clients initialized for {self.character_id}.")

    def _random_delay(self):
        delay = random.randint(15, 45)
        logger.info(f"Sleeping for {delay}s before tweeting...")
        if not self.dry_run:
            time.sleep(delay)

    def publish_tweet(self, text: str, media_path: str = None) -> str:
        """Publishes a single tweet with optional media attachment."""
        logger.info(f"Tweeting to X: '{text[:40]}' (Media: {media_path})")
        if self.dry_run or not self.creds.get("consumer_key") or self.creds.get("consumer_key") == "your_key_here":
            logger.info(f"[SIMULATION] Mock tweet: '{text}'")
            return f"mock_tweet_id_{int(time.time())}"

        self._random_delay()
        if not self.client_v2:
            self._init_client()

        try:
            media_ids = []
            if media_path and os.path.exists(media_path):
                logger.info(f"Uploading media file to X: {media_path}")
                # Upload using v1 API
                res = self.api_v1.media_upload(media_path)
                media_ids.append(res.media_id_string)
                logger.info(f"Uploaded media. Media ID: {res.media_id_string}")

            # Post using v2 Client
            if media_ids:
                resp = self.client_v2.create_tweet(text=text, media_ids=media_ids)
            else:
                resp = self.client_v2.create_tweet(text=text)
                
            tweet_id = resp.data.get("id")
            logger.info(f"Tweet successfully posted! ID: {tweet_id}")
            return str(tweet_id)
        except Exception as e:
            logger.error(f"Failed to post tweet to X: {str(e)}")
            raise e

    def publish_thread(self, tweets: list, media_path: str = None) -> str:
        """Publishes a thread of tweets. Optionally attaches media to the first tweet."""
        logger.info(f"Publishing X thread of {len(tweets)} tweets...")
        if self.dry_run or not self.creds.get("consumer_key") or self.creds.get("consumer_key") == "your_key_here":
            logger.info(f"[SIMULATION] Mock thread of {len(tweets)} tweets.")
            return f"mock_thread_id_{int(time.time())}"

        if not tweets:
            return ""

        self._random_delay()
        if not self.client_v2:
            self._init_client()

        try:
            first_tweet = tweets[0]
            # 1. Post first tweet (with media if present)
            media_ids = []
            if media_path and os.path.exists(media_path):
                logger.info(f"Uploading media for thread-head: {media_path}")
                res = self.api_v1.media_upload(media_path)
                media_ids.append(res.media_id_string)

            if media_ids:
                resp = self.client_v2.create_tweet(text=first_tweet, media_ids=media_ids)
            else:
                resp = self.client_v2.create_tweet(text=first_tweet)
                
            last_tweet_id = resp.data.get("id")
            thread_start_id = last_tweet_id
            logger.info(f"Thread started! First tweet ID: {last_tweet_id}")

            # 2. Post subsequent replies in thread
            for idx, tweet_text in enumerate(tweets[1:]):
                time.sleep(2) # brief delay between thread items
                resp = self.client_v2.create_tweet(
                    text=tweet_text,
                    in_reply_to_tweet_id=last_tweet_id
                )
                last_tweet_id = resp.data.get("id")
                logger.info(f"Thread reply {idx+2}/{len(tweets)} posted. ID: {last_tweet_id}")

            return str(thread_start_id)
        except Exception as e:
            logger.error(f"Failed to post thread to X: {str(e)}")
            raise e

class PublisherRouter:
    @staticmethod
    def publish_post(db, post, dry_run: bool = False) -> str:
        """
        Routes a database ContentPost to the correct social platform.
        Updates state to 'published' or 'failed' and saves details to DB.
        """
        char_id = post.character_id
        platform = post.platform.lower()
        post_type = post.post_type.lower()
        
        logger.info(f"Routing post {post.id} ({char_id} -> {platform}:{post_type})")
        
        try:
            if platform == "instagram":
                if post_type == "reel":
                    # Official Meta Content Publishing API (token-based, no login).
                    # Handles re-encode -> cloudflared host -> container -> poll -> publish.
                    if not post.media_path or not os.path.exists(post.media_path):
                        raise FileNotFoundError(f"Media file not found for reel: {post.media_path}")
                    from src.publishing.official_publisher import OfficialIGPublisher
                    publisher = OfficialIGPublisher(post.character_id)
                    post_id = publisher.post_reel(post.media_path, post.caption or "")
                    post.media_type = "video"
                else:
                    publisher = InstagramPublisher(char_id, dry_run=dry_run)
                    if post_type in ("static", "photo", "quote_card"):
                        if not post.media_path or not os.path.exists(post.media_path):
                            raise FileNotFoundError(f"Media file not found for static photo: {post.media_path}")
                        post_id = publisher.publish_photo(post.media_path, post.caption or "")
                        post.media_type = "photo"
                    elif post_type == "carousel":
                        # Carousel paths are stored in media_path separated by comma or semicolon
                        paths = [p.strip() for p in post.media_path.split(";") if p.strip()]
                        for p in paths:
                            if not os.path.exists(p):
                                raise FileNotFoundError(f"Carousel media file not found: {p}")
                        post_id = publisher.publish_carousel(paths, post.caption or "")
                        post.media_type = "carousel"
                    else:
                        raise ValueError(f"Unsupported IG post type: {post_type}")

            elif platform == "x":
                publisher = XPublisher(char_id, dry_run=dry_run)
                if post_type == "thread":
                    # Script column holds thread list encoded or parsed by lines
                    # If empty, fall back to caption
                    thread_tweets = []
                    if post.script:
                        thread_tweets = [t.strip() for t in post.script.split("\n\n") if t.strip()]
                    if not thread_tweets:
                        thread_tweets = [post.caption]
                        
                    post_id = publisher.publish_thread(thread_tweets, post.media_path)
                    post.media_type = "photo" if post.media_path else "text"
                else:
                    # Standard single tweet/image tweet
                    post_id = publisher.publish_tweet(post.caption or "", post.media_path)
                    post.media_type = "photo" if post.media_path else "text"
            else:
                raise ValueError(f"Unsupported social media platform: {platform}")

            # Successful publish
            post.state = "published"
            post.platform_post_id = post_id
            post.actual_posted_time = type(post.scheduled_time).now() # datetime.utcnow
            post.error_message = None
            db.commit()
            logger.info(f"Post {post.id} successfully published to {platform}! ID: {post_id}")
            return post_id

        except Exception as e:
            # Capture error details
            db.rollback()
            post.retry_count += 1
            if post.retry_count >= 3:
                post.state = "failed"
            else:
                post.state = "scripted" # Return to scripted state so queue manager retries
                
            post.error_message = str(e)
            db.commit()
            logger.error(f"Publishing failed for post {post.id}: {str(e)}")
            raise e
