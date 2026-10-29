"""Shared text-video helpers: PIL frames, audio concat, image-sequence mux."""
import os, sys, logging, subprocess, tempfile, shutil
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
logger = logging.getLogger("content_engine.text_video")

W, H, FPS = 608, 1080, 30


def make_text_bubble_png(speaker, text, color, out_path):
    """Create a PNG image of a text bubble."""
    from PIL import Image, ImageDraw, ImageFont

    font = ImageFont.truetype("C:/Windows/Fonts/arial.ttf", 20) if os.path.exists("C:/Windows/Fonts/arial.ttf") else ImageFont.load_default()

    text = text.encode('ascii', errors='ignore').decode('ascii')
    if len(text) > 60:
        text = text[:57] + "..."

    display_text = "{}: {}".format(speaker, text) if speaker else text

    bbox = font.getbbox(display_text)
    tw = min(bbox[2] - bbox[0] + 24, W // 2 - 40)
    th = bbox[3] - bbox[1] + 16

    r = int(color[0:2], 16)
    g = int(color[2:4], 16)
    b = int(color[4:6], 16)

    img = Image.new('RGBA', (tw, th), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle([0, 0, tw-1, th-1], radius=10, fill=(r, g, b, 220))
    draw.text((12, 6), display_text, fill=(255, 255, 255, 255), font=font)

    img.save(out_path)
    return out_path


def get_audio_duration(audio_path):
    try:
        cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(audio_path)]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        if r.returncode == 0 and r.stdout.strip():
            return float(r.stdout.strip())
    except:
        pass
    return 0.0


def gen_audio(text, voice, out):
    import asyncio, edge_tts
    async def _g():
        await edge_tts.Communicate(text, voice).save(out)
    asyncio.run(_g())


def concat_audio(files, out):
    if len(files) == 1:
        shutil.copy(files[0], out)
        return
    cmd = ["ffmpeg", "-y"]
    parts = []
    for i, f in enumerate(files):
        cmd.extend(["-i", str(f)])
        parts.append("[{}:a]".format(i))
    filt = "".join(parts) + "concat=n={}:v=0:a=1[a]".format(len(files))
    cmd.extend(["-filter_complex", filt, "-map", "[a]", "-acodec", "pcm_s16le", str(out)])
    subprocess.run(cmd, capture_output=True, timeout=60)


def render_text_video(messages, audio_path, output_path):
    """Legacy generic bubble renderer (kept for compatibility)."""
    total_dur = get_audio_duration(audio_path)
    if total_dur <= 0:
        total_dur = len(messages) * 3.5

    msg_dur = total_dur / len(messages)

    tmp = Path(tempfile.mkdtemp())
    concat_file = tmp / "concat.txt"

    with open(concat_file, "w") as f:
        for i, msg in enumerate(messages):
            bubble_path = str(tmp / "bubble_{:03d}.png".format(i))
            make_text_bubble_png(msg.get("speaker", ""), msg.get("text", ""), msg.get("color", "007AFF"), bubble_path)

            clip_path = str(tmp / "clip_{:03d}.mp4".format(i))

            is_left = (i % 2 == 0)
            x = 80 if is_left else W // 2 + 40
            y = 200 + (i % 6) * 80

            src = "color=c=#1a1a2e:s={}x{}:d={}:r={}".format(W, H, msg_dur, FPS)

            cmd = [
                "ffmpeg", "-y",
                "-f", "lavfi", "-i", src,
                "-i", bubble_path,
                "-filter_complex", "[0:v][1:v]overlay={}:{}".format(x, y),
                "-c:v", "libx264", "-preset", "fast", "-crf", "23",
                "-pix_fmt", "yuv420p", "-an",
                "-movflags", "+faststart",
                clip_path
            ]
            subprocess.run(cmd, capture_output=True, timeout=60)

            f.write("file '{}'\n".format(clip_path))

    cmd = [
        "ffmpeg", "-y",
        "-f", "concat", "-safe", "0",
        "-i", str(concat_file),
        "-i", str(audio_path),
        "-c:v", "copy", "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart",
        str(output_path)
    ]

    r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)

    shutil.rmtree(tmp, ignore_errors=True)

    if r.returncode != 0:
        logger.error("render error: %s", r.stderr[-300:])

    return str(output_path)


def render_phone_call(messages, audio_path, output_path, caller="Goku", callee="Vegeta"):
    """Render an iOS-style phone call: incoming screen → active call UI."""
    from PIL import Image, ImageDraw, ImageFont

    total_dur = get_audio_duration(audio_path)
    if total_dur <= 0:
        total_dur = len(messages) * 3.5
    msg_dur = total_dur / len(messages)

    tmp = Path(tempfile.mkdtemp())
    frames_dir = tmp / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)

    # Fonts
    try:
        font_name = ImageFont.truetype("C:/Windows/Fonts/arialbd.ttf", 32)
        font_label = ImageFont.truetype("C:/Windows/Fonts/arial.ttf", 18)
        font_small = ImageFont.truetype("C:/Windows/Fonts/arial.ttf", 14)
        font_tiny = ImageFont.truetype("C:/Windows/Fonts/arial.ttf", 11)
    except:
        font_name = ImageFont.load_default()
        font_label = font_name
        font_small = font_name
        font_tiny = font_name

    # Avatars
    def get_sprite(name):
        base = "D:/Open Projects/Content Engine/data/characters"
        char_dir = f"{base}/{name.lower()}/sprites"
        if os.path.exists(char_dir):
            sprites = sorted(os.listdir(char_dir))
            if sprites:
                return f"{char_dir}/{sprites[0]}"
        base2 = "D:/Open Projects/Content Engine/data/sprites_clean"
        for f in os.listdir(base2):
            if name.lower() in f.lower():
                return f"{base2}/{f}"
        return None

    def make_avatar(name, size):
        sprite = get_sprite(name)
        if sprite:
            try:
                img = Image.open(sprite).convert("RGBA")
                img = img.resize((size, size))
                mask = Image.new("L", (size, size), 0)
                ImageDraw.Draw(mask).ellipse([0, 0, size-1, size-1], fill=255)
                out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
                out.paste(img, (0, 0), mask)
                return out
            except:
                pass
        colors = {"goku": (255, 140, 0), "vegeta": (30, 100, 200)}
        color = colors.get(name.lower(), (150, 150, 150))
        img = Image.new("RGBA", (size, size), color)
        ImageDraw.Draw(img).ellipse([0, 0, size-1, size-1], fill=color)
        return img

    caller_avatar = make_avatar(caller, 90)
    callee_avatar = make_avatar(callee, 90)

    frame_idx = 0

    # Phase 1: Incoming call screen (2 sec)
    incoming_frames = int(2 * FPS)
    for _ in range(incoming_frames):
        img = Image.new("RGB", (W, H), (26, 26, 46))
        d = ImageDraw.Draw(img)

        # Callee avatar large centered
        img.paste(callee_avatar, (W//2 - 45, 160), callee_avatar)
        d.text((W//2 - 40, 270), callee, fill=(255, 255, 255), font=font_name)
        d.text((W//2 - 30, 310), "mobile", fill=(150, 150, 150), font=font_label)

        # Slide to answer
        d.text((W//2 - 70, H - 130), "slide to answer", fill=(200, 200, 200), font=font_label)

        # Accept / decline buttons
        d.ellipse([80, H - 210, 160, H - 130], fill=(50, 200, 50))
        d.text((108, H - 185), "✓", fill=(255, 255, 255), font=font_name)
        d.ellipse([W - 160, H - 210, W - 80, H - 130], fill=(220, 50, 50))
        d.text((W - 132, H - 185), "✗", fill=(255, 255, 255), font=font_name)

        img.save(str(frames_dir / f"frame_{frame_idx:06d}.png"))
        frame_idx += 1

    # Phase 2: Active call screen
    active_frames = int((total_dur - 2) * FPS)
    for f in range(active_frames):
        elapsed = f / FPS
        msg_idx = min(int(elapsed / msg_dur), len(messages) - 1)
        speaking = messages[msg_idx]["speaker"]

        img = Image.new("RGB", (W, H), (26, 26, 46))
        d = ImageDraw.Draw(img)

        # Small callee avatar top center
        small = callee_avatar.resize((35, 35))
        img.paste(small, (W//2 - 17, 50), small)
        d.text((W//2 - 25, 95), callee, fill=(255, 255, 255), font=font_label)

        # Timer
        mins = int(elapsed) // 60
        secs = int(elapsed) % 60
        d.text((W//2 - 20, 125), f"{mins}:{secs:02d}", fill=(200, 200, 200), font=font_small)

        # Speaking indicator
        if speaking:
            who = caller if "goku" in speaking.lower() else callee
            d.text((W//2 - 40, 150), f"{who} is talking...", fill=(0, 200, 100), font=font_tiny)

        # 6-button grid (2x3)
        buttons = [
            ("Speaker", W//2 - 90, H//2 + 30),
            ("Mute", W//2 + 30, H//2 + 30),
            ("Keypad", W//2 - 90, H//2 + 110),
            ("Add call", W//2 + 30, H//2 + 110),
            ("FaceTime", W//2 - 90, H//2 + 190),
            ("Contacts", W//2 + 30, H//2 + 190),
        ]
        for label, bx, by in buttons:
            d.ellipse([bx, by, bx+50, by+50], fill=(60, 60, 60))
            d.text((bx + 5, by + 55), label, fill=(180, 180, 180), font=font_tiny)

        # End call button
        d.rounded_rectangle([W//2 - 40, H - 90, W//2 + 40, H - 45], radius=20, fill=(220, 50, 50))
        d.text((W//2 - 15, H - 78), "end", fill=(255, 255, 255), font=font_label)

        img.save(str(frames_dir / f"frame_{frame_idx:06d}.png"))
        frame_idx += 1

    # Mux frames + audio
    cmd = [
        "ffmpeg", "-y", "-framerate", str(FPS),
        "-i", str(frames_dir / "frame_%06d.png"),
        "-i", str(audio_path),
        "-c:v", "libx264", "-preset", "fast", "-crf", "23",
        "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k",
        "-shortest", "-movflags", "+faststart",
        str(output_path)
    ]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)

    shutil.rmtree(tmp, ignore_errors=True)

    if r.returncode != 0:
        logger.error("render error: %s", r.stderr[-300:])

    return str(output_path)
