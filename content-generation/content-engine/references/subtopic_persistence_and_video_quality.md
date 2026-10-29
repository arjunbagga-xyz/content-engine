# Subtopic persistence + video quality fix — verified session (2026-09-10)

Companion reference to SKILL.md. Verified evidence (not assumptions) from files, DB, logs.

## Subtopic persistence defense (generate_job.py patch)

Root: the planner (`plan_job.py` 147) calls `SUB.resolve_subtopic()` (LLM produces concrete topics), writes `topic=topic` onto `ContentPost` (line 233), but the pipeline previously had NO defensive check that `topic` is non-empty before generate runs. Legacy `planned` rows (`post 128-131` from 2026-08-31) had `topic=None` (the `topic` column didn't exist / wasn't set), and the generate step would silently fall back to the raw YAML `topics` array (`"fitness & training"`, `"discipline & routine"` etc.) instead of the concrete LLM-resolved subtopic — producing generic debate garbage.

Fix added to `src/jobs/generate_job.py` (lines 65-73, verified by syntax check):
- Before `self.logger.info(...)`: check `if not topic or not str(topic).strip()`.
- Fallback: `topic = (post.topic or "").strip()` (use DB-stored concrete subtopic).
- If STILL empty: `raise RuntimeError(f"post {post.id} has no concrete subtopic (post.topic={post.topic!r}); planner did not resolve subtopic. Check subtopic_io.log and plan_job.py.")`.
- This makes a missing subtopic a LOUD failure (not silent generic output).

Verified tests:
- `subtopic_io.log` (real, exists): 4 concrete `tate_vs_peppa` entries ("2025 UN Ocean Treaty...", "OPEC+ production cuts...", etc.) — LLM working.
- DB (`ContentPost.topic` column exists at `db.py` 70); planner writes it (line 233); defensive check reads it back.
- `post 213` (test post created with forced `posts_per_day=5`, then reverted): `topic` = `"The 2025 United Nations Ocean Treaty..."` (non-empty concrete); `tone_plan` = JSON with `char: ["furious","defiant","skeptical","agitated","weary","resolute"]`.
- `generate_job.py` compiles OK (`python -m py_compile`).

No destructive action: only added a defensive block (9 lines) to an existing job file; no DB deletion; no pipeline interruption.

## Video quality degradation — publish re-encode (official_publisher.py patch)

Root: the GENERATE pipeline (`video.py`) uses `h264_nvenc -preset p1 -cq 23` (NVENC, ~10s/reel, high quality). The PUBLISH re-encode (`official_publisher.py` `reencode()` lines 75-87) previously used `libx264 -preset veryfast -crf 38 -maxrate 1000k -bufsize 2000k -b:a 96k` — very compressed CPU encode, low audio bitrate, low maxrate. This degrades the output visibly (blocky, poor audio) compared to the generate step.

Fix applied to `src/publishing/official_publisher.py` (verified by grep):
- `-preset veryfast` → `-preset medium`
- `-crf 38` → `-crf 23`
- `-maxrate 1000k -bufsize 2000k` → `-maxrate 5000k -bufsize 10000k`
- `-b:a 96k` → `-b:a 192k`
- `-c:v libx264` kept (publish uses CPU; NVENC at publish time is optional, `video.py` uses NVENC for generate only).

Isolated verification (`scratch/test_video_isolation.py`):
- Created working copy (`Desktop/gameplay/spodermin_work.mp4`) from original `Desktop/spodermin.mp4` (original untouched, 129831350 bytes, timestamp `Sep 10 00:40`).
- Ran new settings: `test_quality_new.mp4` = 46MB (non-empty output, real file).
- Confirmed original file untouched (`ls -l` same size / timestamp).
- No `Desktop/gameplay/` files deleted (user handled file cleanup separately; the reference notes the original is preserved and scratch outputs exist only as verification artifacts).

No destructive delete performed (user's standing rule confirmed: only edited `official_publisher.py` + added defensive block; did NOT delete backups or run `rm -rf`).

## Pipeline summary (verified live state at session time of this reference)
- Active account: `tate_vs_peppa` (`status=active` in `characters.yaml` line 332).
- Pipeline: planner (`run_job.py plan`, 6x/day via `CE_plan_*`) → dispatcher (`run_job.py dispatch --batch`) → generate (`run_job.py generate --post-id N`) → publish (`run_job.py publish --post-id N`).
- RVC: persistent CUDA worker (`RVC_CUDA_GRAPH="0"`, single-slot `_CACHE` eviction, `subprocess` ffmpeg decode + direct `pipeline.pipeline`, `0x08000000` hidden window). Zero OOM on 4-char test (tate/peppa/trump/hulk).
- Video: `video.py` `vcodec_args()` = `h264_nvenc -preset p1 -cq 23` (generate); `official_publisher.py` = fixed `medium/crf23/5000k/192k` (publish).
- Subtopic: planner resolves concrete topic (LLM, `subtopic.py`); defensive check in `generate_job.py` ensures `post.topic` is non-empty before rendering. No more silent generic garbage.
- Lock: dispatcher `_acquire_lock` uses `acquired` flag (no self-skip), `_pid_alive` uses strict psutil cmdline check (`run_job`/`dispatch` verification), zombie removal uses rename-aside + `rmtree`, `rc==3` (not-due) path commits re-queue, `GEN_TIMEOUT_S=3600`.
- Autopilot tasks (`CE_plan_*`): hidden (`<Hidden>true</Hidden>` via XML edit + elevated `schtasks /create` with `/f` — user executed the command in admin terminal; verified live definitions carry the flag).
