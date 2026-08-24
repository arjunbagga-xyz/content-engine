# Official Instagram Publishing Setup (Meta Content Publishing API)

Decision record: tate_vs_peppa posts via Meta's **official** Content Publishing API
(`graph.instagram.com`). The unofficial instagrapi path stays in the repo as dead
fallback only — it hits OTP/challenge walls on new IPs, breaks on library updates,
and carries real ban risk on an account we care about.

---

## 1. Why this solves our problems

| Problem with instagrapi | Official API |
|---|---|
| OTP / ChallengeRequired on login | OAuth-style token granted once per account |
| Session invalidation cycles | Long-lived token (~60 days), refreshable indefinitely |
| Library breakage (urllib3 shim etc.) | Plain HTTPS REST, no client fingerprinting |
| Account ban risk | ToS-compliant — this IS the sanctioned path |
| 3 posts/day | Limit is 100 API posts / 24h **per account** |

What it does NOT solve: content-policy risk (Tate/Trump voice clones can still be
reported/removed). Different problem, unchanged by transport.

## 2. Requirements

1. **Instagram Professional account** (Business or Creator) — free, converted in
   the IG app: Settings → Account type → Switch to Professional. Personal accounts
   cannot use this API. Do this for EVERY account we post to.
2. **Meta developer account + app**: https://developers.facebook.com → My Apps →
   Create App → type **Business**. Add the **Instagram** product
   ("Instagram API with Instagram Login" — the modern path; NO Facebook Page
   required, unlike the older Facebook-Login path).
3. **App roles for each account** (Development mode): App Dashboard → App roles →
   Roles → add each IG username as an **Instagram Tester**. Each account must
   accept the invite (IG app → Settings → Website permissions → Apps and websites).
   Only app-role accounts can authorize while the app is in Development mode.
   App Review / Advanced Access is ONLY needed to post to strangers' accounts —
   not ours.
4. Redirect URI: in the Instagram product settings set e.g.
   `https://localhost:8517/callback` (nothing needs to listen there during manual
   setup — see §4 trick).

## 3. Config layout (multi-account)

Tokens are **per account**, stored in `.env` (never committed):

```
# --- Official IG API ---
META_APP_ID=<app id>
META_APP_SECRET=<app secret>
IG_OFFICIAL_tate_vs_peppa_TOKEN=<long-lived token>
IG_OFFICIAL_tate_vs_peppa_USER_ID=<numeric ig user id>
# future accounts: repeat the two IG_OFFICIAL_<account_id>_* lines
```

Account ids match `characters.yaml` ids exactly (`tate_vs_peppa`, ...). The
publisher looks up by account id — adding account #2 is: convert to Pro → accept
tester invite → run §4 once → add two `.env` lines. No code change.

## 4. One-time authorization per account (manual, ~5 min)

Run these in a browser/terminal. Values go to `.env` afterwards.

```
# 4.1 Open in browser (fill APP_ID + REDIRECT_URI):
https://www.instagram.com/oauth/authorize
    ?client_id=<APP_ID>
    &redirect_uri=<REDIRECT_URI>
    &response_type=code
    &scope=instagram_business_basic,instagram_business_content_publish

# Log in AS the target account, click Allow.
# You get redirected to REDIRECT_URI?code=AQDx... — copy the CODE.
# (Trick: redirect_uri can point at localhost; the browser will show an error
#  page but the URL bar contains the code. Copy it from there.)

# 4.2 Code -> short-lived token (valid 1h):
curl -X POST https://api.instagram.com/oauth/access_token \
  -d client_id=<APP_ID> -d client_secret=<APP_SECRET> \
  -d grant_type=authorization_code \
  -d redirect_uri=<REDIRECT_URI> \
  -d code=<CODE_FROM_41>

# 4.3 Short-lived -> LONG-LIVED token (~60 days):
curl -G "https://graph.instagram.com/access_token" \
  --data-urlencode grant_type=ig_exchange_token \
  --data-urlencode client_secret=<APP_SECRET> \
  --data-urlencode access_token=<SHORT_TOKEN>

# 4.4 Who am I (get numeric user id for endpoints):
curl -G "https://graph.instagram.com/me" \
  --data-urlencode fields=user_id,username \
  --data-urlencode access_token=<LONG_TOKEN>
```

Store `LONG_TOKEN` + `user_id` per account. **Token refresh** — long-lived tokens
expire ~60 days; refreshing is one call and can be done any time while valid
(schedule monthly, e.g. cron):

```
curl -G "https://graph.instagram.com/refresh_access_token" \
  --data-urlencode grant_type=ig_refresh_token \
  --data-urlencode access_token=<CURRENT_LONG_TOKEN>
```

Response contains the fresh long-lived token → overwrite the `.env` line.
If a token ever lapses fully, just rerun §4 (needs the account password — keep
them in the desktop cred files like tvp.txt).

## 5. Posting a reel (the runtime flow)

Three-step container flow. Video must be reachable at a **public HTTPS URL**
while Meta fetches and processes it (see §6 for the temporary-exposure pattern).

```python
import requests, time

GRAPH = "https://graph.instagram.com/v21.0"
TOKEN = ...           # IG_OFFICIAL_<acct>_TOKEN
IG_ID  = ...          # IG_OFFICIAL_<acct>_USER_ID

# Step 1 — create container (Meta will cURL video_url itself)
c = requests.post(f"{GRAPH}/{IG_ID}/media", data={
    "media_type": "REELS",
    "video_url":  PUBLIC_URL,          # our tunnel/VPS/object-storage link
    "caption":    CAPTION,
    "share_to_feed": "true",
    "access_token": TOKEN,
}).json()
container = c["id"]

# Step 2 — poll until processed (docs: 1x/min, max ~5 min)
while True:
    s = requests.get(f"{GRAPH}/{container}",
                     params={"fields": "status_code", "access_token": TOKEN}).json()
    if s["status_code"] == "FINISHED": break
    if s["status_code"] in ("ERROR", "EXPIRED"): raise RuntimeError(s)
    time.sleep(20)

# Step 3 — publish
m = requests.post(f"{GRAPH}/{IG_ID}/media_publish",
                  data={"creation_id": container, "access_token": TOKEN}).json()
permalink = requests.get(f"{GRAPH}/{m['id']}",
                         params={"fields":"permalink","access_token":TOKEN}).json()
```

Statuses: `IN_PROGRESS` → `FINISHED` → (after publish) `PUBLISHED`.
Containers expire if not published within **24 h**.
Rate limit: 100 publishes / 24 h / account — check with
`GET /{IG_ID}/content_publishing_limit`.

### Pre-publish re-encode (mandatory in practice)

Our renderer outputs ~500 MB (`ultrafast crf 26`). Re-encode before exposing:
`ffmpeg -i in.mp4 -c:v libx264 -preset medium -crf 28 -pix_fmt yuv420p -c:a aac -b:a 128k out.mp4`
→ typically 30–80 MB for 2–8 min, uploads through a home line in under a minute,
and IG transcodes happily. Exact max size/duration for REELS containers: check
the current Content Publishing reference (limits shift between versions);
1080×1920 MP4 (H.264 + AAC) is the always-safe baseline.

## 6. Media hosting — the "temporary exposure" pattern (user's idea, confirmed)

Meta only needs the URL **during container creation + processing**, i.e. from
step 1 until `status_code=FINISHED`. After that the copy lives on Meta's servers;
our URL can die. So: spin up a local file server + tunnel, post, poll, tear down.
Bonus: the reel never sits on permanent public storage.

Options, best-first for us:

| Option | Notes |
|---|---|
| **cloudflared quick tunnel** | `cloudflared tunnel --url http://localhost:8517` → random `*.trycloudflare.com` HTTPS URL, no account, no bandwidth metering observed. Recommended default. |
| **ngrok** | Same idea; free tier wants a signup, gives static domain on paid. Fine too. |
| **OCI/VPS nginx** | We already run boxes; `python -m http.server` behind Caddy/nginx on a subdomain. Most stable if we later want retries. |
| **R2/S3/B2 + lifecycle delete** | Zero-maintenance, ~$0 at our volume; delete object after `PUBLISHED`. Best if the PC is off at posting time. |

Runtime sequence (what `official_publisher.py` will automate):

1. re-encode → `outputs/<name>_web.mp4`
2. `python -m http.server 8517 -d outputs` + `cloudflared tunnel --url http://localhost:8517` → parse the public URL
3. container create with `PUBLIC_URL/<file>` → poll to FINISHED (tunnel must stay UP here)
4. `media_publish` → confirm permalink
5. kill tunnel + server; optional: delete `_web.mp4`

Failure handling: if the tunnel dies mid-processing, the container errors →
just recreate container with a fresh URL (containers are disposable).

## 7. Multi-account summary

- N accounts = N×(Pro conversion + tester accept + §4 run) + 2 `.env` lines each.
- Tokens independent; one expiring doesn't affect others.
- Rate limits are per account (100/day each).
- Publisher class takes `account_id`, reads its own token/user-id — identical
  loop for future accounts (influencer accounts included: same API, plus
  `instagram_business_manage_messages` etc. later if we want DMs).

## 8. Gotchas checklist

- [ ] Development mode: poster account MUST be an accepted Instagram Tester.
- [ ] Redirect URI in §4.1 must EXACTLY match the app settings value.
- [ ] Serve with correct `Content-Type: video/mp4` (`http.server` guesses ok for .mp4).
- [ ] Keep tunnel alive until FINISHED (not merely until step 1 returns).
- [ ] Caption: plain text; hashtags fine; no @-mention spam triggers.
- [ ] Schedule token refresh (~every 30 days) — silent 60-day expiry otherwise.
- [ ] Never commit tokens; `.env` only; redact in logs like other secrets.
- [ ] Real-figure voices remain a moderation/report risk — unrelated to transport.

## 9. Repo integration plan

- New: `src/publishing/official_publisher.py` — `OfficialIGPublisher(account_id)`
  implementing §5 + §6 (server+tunnel context manager, poll, publish, cleanup).
- `publisher.py` (instagrapi) stays untouched as legacy fallback, unused by scheduler.
- Scheduler (3×/day) calls `produce_account_debate('tate_vs_peppa', ...)`
  → `OfficialIGPublisher.post_reel(path, caption)`.
- Config: keys in §3 read via `src/core/config.py` dotenv loader.
