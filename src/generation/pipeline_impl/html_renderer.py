"""Playwright-based video renderer using HTML/CSS templates.

Audio sync strategy:
  The visual timeline has gaps (typing indicators between messages).
  We generate silence WAVs matching each gap and interleave them with
  the message audio, so concatenated audio time == visual time, exactly.
  Frame at time T shows the state matching what's audible at T.
"""
import os, sys, json, shutil, subprocess, tempfile, random, asyncio
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

TEMPLATES_DIR = Path(__file__).parent / "templates"
W, H, FPS = 608, 1080, 30


def get_audio_duration(path):
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", str(path)],
            capture_output=True, text=True, timeout=10)
        if r.returncode == 0 and r.stdout.strip():
            return float(r.stdout.strip())
    except Exception:
        pass
    return 0.0


def make_silence(path, duration, rate=40000):
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi",
         "-i", f"anullsrc=r={rate}:cl=mono", "-t", str(duration),
         "-acodec", "pcm_s16le", str(path)],
        capture_output=True, timeout=30)
    return path


class HTMLRenderer:
    """Async Playwright renderer — safe to use inside asyncio.run()."""

    def __init__(self, template_name):
        self.template_name = template_name
        self._pw = None
        self.browser = None
        self.page = None

    async def start(self):
        from playwright.async_api import async_playwright
        self._pw = await async_playwright().start()
        self.browser = await self._pw.chromium.launch(
            args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"],
        )
        self.page = await self.browser.new_page(viewport={"width": W, "height": H})
        template = TEMPLATES_DIR / self.template_name
        await self.page.goto(f"file:///{template.as_posix()}")
        await self.page.wait_for_load_state("load")

    async def screenshot(self, path):
        await self.page.screenshot(path=str(path))

    async def screenshot_jpeg(self, quality=90):
        return await self.page.screenshot(type="jpeg", quality=quality)

    async def evaluate(self, expr, arg=None):
        return await self.page.evaluate(expr, arg)

    async def close(self):
        if self.browser:
            await self.browser.close()
        if self._pw:
            await self._pw.stop()


def build_padded_audio(audio_files, gaps, lead_in, tail, tmp_dir):
    """Concatenate audio with silence gaps so audio time == visual time.

    gaps: [gap_before_msg1, gap_before_msg2, ...] (len = len-1)
    Returns (combined_path, events, total_duration) where events[i] has
    the exact start/end time of each message's audio in the combined file.
    """
    parts = [make_silence(tmp_dir / "lead.wav", lead_in)] if lead_in > 0 else []
    events = []
    t = lead_in
    for i, af in enumerate(audio_files):
        if i > 0 and gaps[i - 1] > 0:
            parts.append(make_silence(tmp_dir / f"gap_{i}.wav", gaps[i - 1]))
            t += gaps[i - 1]
        dur = get_audio_duration(af)
        events.append({"start": t, "end": t + dur, "dur": dur})
        parts.append(Path(af))
        t += dur
    if tail > 0:
        parts.append(make_silence(tmp_dir / "tail.wav", tail))
    total = t + tail

    combined = tmp_dir / "combined.wav"
    if len(parts) == 1:
        shutil.copy(parts[0], combined)
    else:
        cmd = ["ffmpeg", "-y"]
        filt = ""
        for i, p in enumerate(parts):
            cmd.extend(["-i", str(p)])
            filt += f"[{i}:a]"
        filt += f"concat=n={len(parts)}:v=0:a=1[a]"
        cmd.extend(["-filter_complex", filt, "-map", "[a]",
                    "-acodec", "pcm_s16le", str(combined)])
        r = subprocess.run(cmd, capture_output=True, timeout=300)
        if r.returncode != 0:
            raise RuntimeError(f"audio concat failed: {r.stderr[-200:]}")

    return str(combined), events, total


async def render_video_piped(render_frame_fn, total_frames, audio_path, out_path, fps=FPS):
    """Stream frames to ffmpeg via stdin — no PNG files on disk.

    render_frame_fn(f) may return a PIL Image or an awaitable resolving to one.
    Uses asyncio.create_subprocess_exec so we can await stdin/stdout safely.
    """
    cmd = [
        "ffmpeg", "-y",
        "-f", "rawvideo", "-vcodec", "rawvideo",
        "-s", f"{W}x{H}", "-pix_fmt", "rgb24",
        "-r", str(fps), "-i", "pipe:0",
        "-i", str(audio_path),
        "-c:v", "libx264", "-preset", "fast", "-crf", "23",
        "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k",
        "-shortest", "-movflags", "+faststart", str(out_path),
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        for f in range(total_frames):
            img = render_frame_fn(f)
            if asyncio.iscoroutine(img):
                img = await img
            proc.stdin.write(img.tobytes())
        await proc.stdin.drain()
        proc.stdin.close()
        await proc.wait()
    except BrokenPipeError:
        await proc.wait()
        raise RuntimeError("piped ffmpeg died mid-render (broken pipe)")
    if proc.returncode != 0:
        raise RuntimeError(f"piped ffmpeg failed rc={proc.returncode}")
    return out_path


async def render_video(frames_dir, audio_path, out_path, fps=FPS):
    cmd = [
        "ffmpeg", "-y", "-framerate", str(fps),
        "-i", str(Path(frames_dir) / "frame_%06d.png"),
        "-i", str(audio_path),
        "-c:v", "libx264", "-preset", "fast", "-crf", "23",
        "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k",
        "-shortest", "-movflags", "+faststart", str(out_path),
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {stderr.decode()[-300:]}")
    return out_path


# ============ WhatsApp Chat ============

async def render_whatsapp_chat(messages, audio_files, out_path,
                                contact_name="Vegeta", contact_avatar=None,
                                me_avatar=None, fillers=None, seed=None):
    tmp = Path(tempfile.mkdtemp())
    frames_dir = tmp / "frames"
    frames_dir.mkdir()

    rng = random.Random(seed)

    # Random gaps between messages (typing time). Longer gap sometimes
    # to convey thinking/anger; short gap for quick back-and-forth.
    gaps = []
    for i in range(1, len(messages)):
        quick_reply = messages[i]["speaker"] != messages[i - 1]["speaker"]
        # quick replies come faster; same-speaker follow-ups slower
        gaps.append(rng.uniform(0.4, 1.2) if quick_reply else rng.uniform(1.0, 2.5))

    lead_in = 1.5
    tail = 2.0
    combined, events, total = build_padded_audio(
        audio_files, gaps, lead_in, tail, tmp)

    fillers = fillers or [
        {"speaker": "Goku", "text": "yo"},
        {"speaker": "Vegeta", "text": "what"},
        {"speaker": "Goku", "text": "you at the gym?"},
    ]

    r = HTMLRenderer("whatsapp_chat.html")
    try:
        await r.start()
        await r.evaluate(
            "([n, a]) => window.setContact(n, a, 'online')",
            [contact_name, contact_avatar or ""])

        total_frames = int(total * FPS)
        for f in range(total_frames):
            t = f / FPS

            visible = list(fillers)
            typing = None
            for i, ev in enumerate(events):
                if t >= ev["start"]:
                    visible.append(messages[i])
                else:
                    # Before this message's audio: typing indicator during gap
                    gap_start = ev["start"] - (gaps[i - 1] if i > 0 else lead_in)
                    if t >= gap_start:
                        typing = messages[i]["speaker"]
                    break

            state_msgs = [{
                "sent": m["speaker"] == "Goku",
                "speaker": m["speaker"],
                "text": m["text"],
                "time": "12:34",
                "read": True,
            } for m in visible]

            state = {
                "messages": state_msgs,
                "typing": ("me" if typing == "Goku" else "contact") if typing else None,
                "timestamp": "TODAY",
                "meAvatar": me_avatar or "",
                "contactAvatar": contact_avatar or "",
            }
            await r.evaluate("s => window.setChatState(s)", state)
            await r.screenshot(frames_dir / f"frame_{f:06d}.png")

        await render_video(frames_dir, combined, out_path, FPS)
    finally:
        await r.close()
        shutil.rmtree(tmp, ignore_errors=True)
    return out_path


# ============ X Feed ============

async def render_x_feed(posts, audio_files, out_path, avatars=None, seed=None):
    tmp = Path(tempfile.mkdtemp())
    frames_dir = tmp / "frames"
    frames_dir.mkdir()

    rng = random.Random(seed)
    gaps = [rng.uniform(0.3, 0.9) for _ in range(len(audio_files) - 1)]
    lead_in = 1.0
    tail = 1.5
    combined, events, total = build_padded_audio(
        audio_files, gaps, lead_in, tail, tmp)

    avatars = avatars or {}
    r = HTMLRenderer("x_feed.html")
    try:
        await r.start()
        total_frames = int(total * FPS)
        for f in range(total_frames):
            t = f / FPS

            visible = []
            for i, ev in enumerate(events):
                if t >= ev["start"]:
                    p = dict(posts[i])
                    p["avatar"] = avatars.get(p["user"], "")
                    visible.append(p)
                else:
                    break

            await r.evaluate("p => window.setFeedState({posts: p})", visible)
            await r.screenshot(frames_dir / f"frame_{f:06d}.png")

        await render_video(frames_dir, combined, out_path, FPS)
    finally:
        await r.close()
        shutil.rmtree(tmp, ignore_errors=True)
    return out_path


# ============ WhatsApp Call ============

async def render_whatsapp_call(messages, audio_files, out_path,
                               caller_name="Vegeta", caller_avatar=None, seed=None):
    tmp = Path(tempfile.mkdtemp())
    frames_dir = tmp / "frames"
    frames_dir.mkdir()

    rng = random.Random(seed)
    # Conversational pauses in a call: short
    gaps = [rng.uniform(0.2, 0.7) for _ in range(len(audio_files) - 1)]
    lead_in = 0.0
    tail = 1.5
    combined, events, total = build_padded_audio(
        audio_files, gaps, lead_in, tail, tmp)

    incoming_dur = 2.5
    # The call audio starts AFTER the incoming-call screen. Pad the front.
    # NOTE: -t must come AFTER the input it truncates. anullsrc is infinite,
    # so we MUST limit it with -t on the OUTPUT of input 0, else concat hangs.
    padded = tmp / "call_audio.wav"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi",
         "-i", f"anullsrc=r=40000:cl=mono",
         "-i", combined,
         "-filter_complex", f"[0:a]atrim=duration={incoming_dur}[sil];"
                            f"[sil][1:a]concat=n=2:v=0:a=1[a]",
         "-map", "[a]", "-acodec", "pcm_s16le", str(padded)],
        capture_output=True, timeout=120)

    full_total = incoming_dur + total
    total_frames = int(full_total * FPS)
    r = HTMLRenderer("whatsapp_call.html")

    import io
    from PIL import Image

    async def frame_fn(f):
        t = f / FPS
        if t < incoming_dur:
            state = {"phase": "incoming", "name": caller_name,
                     "avatar": caller_avatar or ""}
        else:
            call_t = t - incoming_dur
            speaking = None
            for i, ev in enumerate(events):
                if ev["start"] <= call_t < ev["end"]:
                    speaking = messages[i]["speaker"]
                    break
            state = {"phase": "active", "name": caller_name,
                     "avatar": caller_avatar or "",
                     "elapsed": call_t, "speaking": speaking}
        await r.evaluate("s => window.setCallState(s)", state)
        # JPEG is ~10x faster to encode than PNG; quality 90 is visually
        # indistinguishable for UI screenshots
        jpg_bytes = await r.screenshot_jpeg(quality=90)
        img = Image.open(io.BytesIO(jpg_bytes)).convert("RGB")
        return img

    try:
        await r.start()
        await render_video_piped(frame_fn, total_frames, str(padded), out_path, FPS)
    finally:
        await r.close()
        shutil.rmtree(tmp, ignore_errors=True)
    return out_path
