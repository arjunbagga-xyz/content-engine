# Official IG Publishing — publish flow (updated 2026-09-14)

## Current flow: tunnel-URL fetch (not direct upload)

Meta's REELS API **requires** `video_url` for container creation. The old resumable
rupload flow (`upload_type: resumable` without `video_url`) returns HTTP 400.

The `_post_reel_direct()` method in `official_publisher.py` now uses the tunnel-URL flow:

1. **Start tunnel** → `tunnel_mgr.ensure_tunnel()` → gets public URL
   (e.g., `https://squeamishly-nestlike-selena.ngrok-free.dev`)
2. **Create container** → POST `/ig_user_id/media` with `video_url` pointing to
   `https://<tunnel>/<video_filename>.mp4`
3. **Meta fetches** → Meta's servers download the video from your tunnel URL
4. **Poll** → GET `/container_id?fields=status_code` until `FINISHED`
5. **Publish** → POST `/ig_user_id/media_publish` with `creation_id`

## Why the tunnel is mandatory

- CGNAT (Airtel/Indian ISPs) kills port forwarding → no public endpoint
- Without a static IP from the ISP, the tunnel is the ONLY way to expose a public URL
- ngrok free tier: unreliable for >150 MB files → hence the 608x1080 @ 45fps reduction (~65 MB)

## Container creation payload

```json
{
  "media_type": "REELS",
  "video_url": "https://<tunnel>/post_255_fitness_ball_z_reel.mp4",
  "caption": "...",
  "share_to_feed": "true"
}
```

Response: `{"id": "18116979967769662"}` — no `uri` field (Meta ignores it now).

## Poll response

```json
{"status_code": "IN_PROGRESS", ...}  // Meta fetching video
{"status_code": "FINISHED", ...}     // Ready to publish
{"status_code": "ERROR", ...}        // Container failed (generic error message)
```

Typical fetch time: 30-120s depending on file size + tunnel bandwidth.

## Error: "The parameter video_url is required" (400)

**Cause:** Container creation request missing `video_url` field.
**Fix:** Add `video_url` to the JSON payload (line 244-253 in official_publisher.py).
**Verified working:** 2026-09-14 with fitness_ball_z account.

## Tunnel options

| Tunnel | Free tier | Reliability >150MB | Notes |
|--------|-----------|-------------------|-------|
| ngrok | ✅ (random URL) | ❌ Flaky | Currently used; `https://squeamishly-nestlike-selena.ngrok-free.dev` |
| cloudflared quick | ✅ (random URL) | ⚠️ Better than ngrok | `cloudflared tunnel --url http://127.0.0.1:8520` |
| cloudflared named | ❌ (need domain) | ✅ Stable URL | Requires DNS setup |
| Tailscale Funnel | ✅ (stable URL) | ❌ Bandwidth limit ~100MB/month | Good for small files |

## Reference

- Post 236 (fitness_ball_z): published via ngrok tunnel, `platform_post_id=18161344216486122`
- Post 255 test: 193 MB file → tunnel timeout (IN_PROGRESS → ERROR at ~2 min)
- After size reduction: ~65 MB files should succeed via ngrok
