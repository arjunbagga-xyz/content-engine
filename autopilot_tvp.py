"""Autopilot for the tate_vs_peppa faceless debate account.

Runs ONE full cycle: generate a fresh debate reel (YAML-driven via
SpriteReactor.produce_account_debate) -> publish it via the official Meta
Content Publishing API (OfficialIGPublisher). Designed to be invoked by a
scheduler (cron / Task Scheduler) 3x/day.

Why this wrapper instead of the full ProductionScheduler:
- produce_account_debate is the proven, config-driven debate generator
  (same code path as run_tvp.py, which already posted 2 reels live).
- OfficialIGPublisher handles re-encode -> cloudflared host -> container ->
  poll -> publish. No instagrapi, no login.
- The full scheduler's planner/queue path for faceless reels is untested;
  this reuses only battle-tested code.

Requirements (kept alive by the deployment):
- cloudflared quick tunnel running, forwarding the ranged server port
  (official_publisher reads scratch/cf.log for the public URL).
- .env has IG_OFFICIAL_TATE_VS_PEPPA_TOKEN / _USER_ID (set via
  scratch/meta_token_setup.py).
"""
import asyncio
import sys
import os
import time
import logging
from datetime import datetime
from pathlib import Path

sys.path.insert(0, r"D:\Open Projects\Content Engine")

from src.generation.sprite_reactor import SpriteReactor
from src.publishing.official_publisher import OfficialIGPublisher

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("content_engine.autopilot_tvp")

ACCOUNT = "tate_vs_peppa"
OUT_DIR = Path(r"D:\Open Projects\Content Engine\outputs")
CAPTION_SUFFIX = " #faceless #debate #ai"


async def run_once():
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = str(OUT_DIR / f"tvp_auto_{stamp}.mp4")

    logger.info("=== autopilot cycle start: %s ===", stamp)
    t0 = time.time()

    # 1. generate
    res = await SpriteReactor.produce_account_debate(
        ACCOUNT, out_path,
        topic=None,          # auto from topic_universe
        num_turns=None,      # auto from seg_range [8,16]
        sprite_scale=0.35,
    )
    topic = res.get("topic")
    turns = len(res.get("turns", []))
    logger.info("generated reel topic=%s turns=%d path=%s (%.1fs)",
                topic, turns, res.get("path"), time.time() - t0)

    # 2. publish via official API
    caption = (res.get("caption") or f"{topic} debate").strip() + CAPTION_SUFFIX
    pub = OfficialIGPublisher(ACCOUNT)
    media_id = pub.post_reel(res.get("path"), caption)
    logger.info("PUBLISHED media_id=%s (total %.1fs)", media_id, time.time() - t0)
    return media_id


if __name__ == "__main__":
    try:
        mid = asyncio.run(run_once())
        print("AUTOPILOT OK media_id=", mid)
    except Exception as e:
        logger.exception("autopilot cycle failed: %s", e)
        raise SystemExit(1)
