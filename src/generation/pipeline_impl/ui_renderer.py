# UI Renderer - composites PNG assets for video frames
import os, sys, tempfile, shutil, subprocess, json, random
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

UI_DIR = "D:/Open Projects/Content Engine/data/ui"

# Load assets
def load_asset(category, name):
    path = f"{UI_DIR}/{category}/{name}"
    if os.path.exists(path):
        return Image.open(path).convert("RGBA")
    return None

# Pre-load all assets
ASSETS = {}
for cat in ["whatsapp", "x", "phone"]:
    ASSETS[cat] = {}
    cat_dir = f"{UI_DIR}/{cat}"
    if os.path.exists(cat_dir):
        for f in os.listdir(cat_dir):
            if f.endswith(".png"):
                ASSETS[cat][f.replace(".png", "")] = Image.open(f"{cat_dir}/{f}").convert("RGBA")

# Fonts
try:
    FONT_TEXT = ImageFont.truetype("C:/Windows/Fonts/arial.ttf", 13)
    FONT_NAME = ImageFont.truetype("C:/Windows/Fonts/arialbd.ttf", 14)
    FONT_SMALL = ImageFont.truetype("C:/Windows/Fonts/arial.ttf", 11)
except:
    FONT_TEXT = ImageFont.load_default()
    FONT_NAME = FONT_TEXT
    FONT_SMALL = FONT_TEXT

def get_sprite(name):
    """Get character sprite."""
    base = "D:/Open Projects/Content Engine/data/characters"
    char_dir = f"{base}/{name.lower()}/sprites"
    if os.path.exists(char_dir):
        sprites = sorted(os.listdir(char_dir))
        if sprites:
            return Image.open(f"{char_dir}/{sprites[0]}").convert("RGBA")
    return None

def make_avatar_circular(img, size):
    """Resize and crop to circle."""
    img = img.resize((size, size))
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).ellipse([0, 0, size-1, size-1], fill=255)
    out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    out.paste(img, (0, 0), mask)
    return out

def wrap_text(text, font, max_width):
    """Word wrap."""
    d = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    words = text.split()
    lines = []
    current = ""
    for word in words:
        test = f"{current} {word}".strip()
        bbox = d.textbbox((0, 0), test, font=font)
        if bbox[2] - bbox[0] > max_width:
            if current:
                lines.append(current)
            current = word
        else:
            current = test
    if current:
        lines.append(current)
    return lines if lines else [""]

def stretch_bubble(bubble, target_width, target_height):
    """Stretch a 9-patch style bubble."""
    # Simple stretch for now - could be improved with proper 9-patch
    return bubble.resize((target_width, target_height))

# === Timeline-based frame generation ===

def build_timeline(audio_files):
    """
    Build timeline from audio files.
    Returns list of (start_time, end_time, audio_path) per message.
    """
    timeline = []
    offset = 0.0
    for af in audio_files:
        dur = get_audio_duration(af)
        timeline.append({
            "start": offset,
            "end": offset + dur,
            "duration": dur,
            "audio": af
        })
        offset += dur
    return timeline, offset

def get_audio_duration(path):
    """Get audio duration using ffprobe."""
    try:
        cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        if r.returncode == 0 and r.stdout.strip():
            return float(r.stdout.strip())
    except:
        pass
    return 0.0

def render_frames_whatsapp(messages, audio_files, output_dir, fps=30):
    """Render frames for WhatsApp chat with proper timing."""
    os.makedirs(output_dir, exist_ok=True)
    
    W, H = 608, 1080
    
    timeline, total_dur = build_timeline(audio_files)
    
    # Add gaps between messages (typing time)
    gap_before = [0.0]  # No gap before first message
    for i in range(1, len(messages)):
        gap = random.uniform(0.8, 2.5)
        gap_before.append(gap)
    
    # Calculate message display times
    msg_times = []
    for i, msg in enumerate(messages):
        if i == 0:
            start = 2.0  # Initial delay to show fillers
        else:
            start = msg_times[i-1]["end"] + gap_before[i]
        msg_times.append({
            "appear": start,
            "audio_start": start,
            "audio_end": start + timeline[i]["duration"],
            "end": start + timeline[i]["duration"] + 0.5
        })
    
    total_frames = int((total_dur + len(messages) * 1.5 + 3) * fps)
    
    # Pre-load avatars
    goku_avatar = get_sprite("Goku")
    vegeta_avatar = get_sprite("Vegeta")
    
    # Fillers
    fillers = [
        {"speaker": "Goku", "text": "hey what's up"},
        {"speaker": "Vegeta", "text": "training. you?"},
        {"speaker": "Goku", "text": "just finished my set"},
    ]
    
    frame_idx = 0
    for f in range(total_frames):
        t = f / fps
        
        # Create frame
        img = Image.new("RGBA", (608, 1080), (237, 229, 221, 255))
        d = ImageDraw.Draw(img)
        
        # Header
        header = ASSETS["whatsapp"].get("header")
        if header:
            img.paste(header, (0, 0), header)
        
        # Back chevron
        d.text((15, 22), "<", fill=(255,255,255), font=FONT_NAME)
        
        # Contact name + avatar (Vegeta - the person you're chatting with)
        if vegeta_avatar:
            av = make_avatar_circular(vegeta_avatar, 32)
            img.paste(av, (50, 16), av)
        d.text((90, 20), "Vegeta", fill=(255,255,255), font=FONT_NAME)
        
        # Messages
        y = 90
        
        # Draw fillers
        for f_msg in fillers:
            is_goku = f_msg["speaker"] == "Goku"
            avatar = goku_avatar if is_goku else vegeta_avatar
            
            if avatar:
                av = make_avatar_circular(avatar, 28)
                if is_goku:
                    img.paste(av, (W-48, y+5), av)
                else:
                    img.paste(av, (12, y+5), av)
            
            text = f_msg["text"]
            lines = wrap_text(text, FONT_TEXT, 160)
            max_w = max(d.textbbox((0,0),l,font=FONT_TEXT)[2]-d.textbbox((0,0),l,font=FONT_TEXT)[0] for l in lines) + 18
            bubble_w = min(max(max_w, 50), 180)
            bubble_h = len(lines)*16 + 12
            
            if is_goku:
                x = W - bubble_w - 55
                color = (217, 253, 211)
                tc = (0, 0, 0)
            else:
                x = 55
                color = (255, 255, 255)
                tc = (0, 0, 0)
            
            d.rounded_rectangle([x, y, x+bubble_w, y+bubble_h], radius=8, fill=color)
            for j, l in enumerate(lines):
                d.text((x+8, y+5+j*16), l, fill=tc, font=FONT_TEXT)
            
            y += bubble_h + 25
            if y > H - 80: break
        
        # Draw messages up to current time
        for i, msg in enumerate(messages):
            if t < msg_times[i]["appear"]:
                # Show typing indicator if in gap
                if i > 0 and t >= msg_times[i-1]["end"] + 0.3:
                    # Typing indicator
                    typing = ASSETS["whatsapp"].get("typing_indicator")
                    if typing:
                        img.paste(typing, (55, y+5), typing)
                continue
            
            is_goku = msg["speaker"] == "Goku"
            avatar = goku_avatar if is_goku else vegeta_avatar
            
            if avatar:
                av = make_avatar_circular(avatar, 28)
                if is_goku:
                    img.paste(av, (W-48, y+5), av)
                else:
                    img.paste(av, (12, y+5), av)
            
            text = msg["text"]
            lines = wrap_text(text, FONT_TEXT, 160)
            max_w = max(d.textbbox((0,0),l,font=FONT_TEXT)[2]-d.textbbox((0,0),l,font=FONT_TEXT)[0] for l in lines) + 18
            bubble_w = min(max(max_w, 50), 180)
            bubble_h = len(lines)*16 + 12
            
            if is_goku:
                x = W - bubble_w - 55
                color = (217, 253, 211)
                tc = (0, 0, 0)
            else:
                x = 55
                color = (255, 255, 255)
                tc = (0, 0, 0)
            
            # Highlight if just appeared
            if msg_times[i]["appear"] <= t < msg_times[i]["appear"] + 0.3:
                d.rounded_rectangle([x-2, y-2, x+bubble_w+2, y+bubble_h+2], radius=10, fill=(255,255,180))
            
            d.rounded_rectangle([x, y, x+bubble_w, y+bubble_h], radius=8, fill=color)
            for j, l in enumerate(lines):
                d.text((x+8, y+5+j*16), l, fill=tc, font=FONT_TEXT)
            
            # Timestamp
            d.text((x+bubble_w-30, y+bubble_h-10), "12:34", fill=(100,100,100), font=FONT_SMALL)
            
            y += bubble_h + 25
            if y > H - 80: break
        
        # Input bar
        input_bg = ASSETS["whatsapp"].get("input_bar_bg")
        if input_bg:
            img.paste(input_bg, (0, H-45), input_bg)
        
        d.text((10, H-28), "😊", font=FONT_TEXT)
        d.rounded_rectangle([45, H-37, W-50, H-15], radius=12, fill=(255,255,255))
        d.text((55, H-28), "Message", fill=(150,150,150), font=FONT_TEXT)
        d.text((W-40, H-28), "🎤", font=FONT_TEXT)
        
        img.save(f"{output_dir}/frame_{frame_idx:06d}.png")
        frame_idx += 1
    
    return frame_idx

# === X Renderer ===

def render_frames_x(messages, audio_files, output_dir, fps=30):
    """Render frames for X comments with proper timing."""
    os.makedirs(output_dir, exist_ok=True)
    
    W, H = 608, 1080
    
    timeline, total_dur = build_timeline(audio_files)
    
    # Calculate message display times
    msg_times = []
    for i, msg in enumerate(messages):
        if i == 0:
            start = 1.0
        else:
            start = msg_times[i-1]["end"] + random.uniform(0.5, 1.5)
        msg_times.append({
            "appear": start,
            "end": start + timeline[i]["duration"] + 0.5
        })
    
    total_frames = int((total_dur + len(messages) * 1.0 + 2) * fps)
    
    goku_avatar = get_sprite("Goku")
    vegeta_avatar = get_sprite("Vegeta")
    
    frame_idx = 0
    for f in range(total_frames):
        t = f / fps
        
        img = Image.new("RGBA", (608, 1080), (0, 0, 0, 255))
        d = ImageDraw.Draw(img)
        
        # Header
        d.text((15, 18), "<", fill=(255,255,255), font=FONT_NAME)
        d.text((W//2-20, 18), "Post", fill=(255,255,255), font=FONT_NAME)
        d.text((W-30, 18), "⋮", fill=(150,150,150), font=FONT_NAME)
        
        y = 65
        
        for i, m in enumerate(messages):
            if t < msg_times[i]["appear"]:
                continue
            
            user = m.get("user", "user")
            is_goku = "goku" in user.lower()
            avatar = goku_avatar if is_goku else vegeta_avatar
            
            # Reply connector
            if m.get("is_reply", i>0) and i > 0:
                d.line([(40, y-12), (40, y+5)], fill=(47,51,54), width=2)
            
            # Avatar
            if avatar:
                av = make_avatar_circular(avatar, 36)
                img.paste(av, (25, y), av)
            
            # Name row
            d.text((75, y), user, fill=(255,255,255), font=FONT_NAME)
            # Verified badge
            verified = ASSETS["x"].get("verified_badge")
            if verified:
                img.paste(verified, (75 + len(user)*8 + 3, y), verified)
            d.text((75 + len(user)*8 + 20, y), f"@{user.lower()}", fill=(113,118,123), font=FONT_SMALL)
            d.text((W-50, y), "1h", fill=(113,118,123), font=FONT_SMALL)
            d.text((W-25, y), "⋮", fill=(113,118,123), font=FONT_SMALL)
            
            # Reply indicator
            text_y = y + 20
            if m.get("is_reply", i>0) and i > 0:
                prev = messages[i-1].get("user","user")
                d.text((75, text_y), f"Replying to @{prev.lower()}", fill=(113,118,123), font=FONT_SMALL)
                text_y += 14
            
            # Text
            text = m.get("text", "")
            lines = wrap_text(text, FONT_TEXT, 460)
            for j, l in enumerate(lines):
                d.text((75, text_y + j*16), l, fill=(255,255,255), font=FONT_TEXT)
            
            text_end = text_y + len(lines)*16 + 5
            
            # Action bar
            d.text((75, text_end), "💬  🔁  ❤️  📊  🔗", fill=(113,118,123), font=FONT_TEXT)
            
            y = text_end + 28
            d.line([(25, y), (W-25, y)], fill=(47,51,54), width=1)
            y += 6
            
            if y > H - 50: break
        
        # Bottom nav
        d.rectangle([0, H-45, W, H], fill=(0,0,0))
        d.line([(0, H-45), (W, H-45)], fill=(47,51,54), width=1)
        d.text((55, H-30), "🏠", font=FONT_TEXT)
        d.text((165, H-30), "🔍", font=FONT_TEXT)
        d.text((275, H-30), "🔔", font=FONT_TEXT)
        d.text((385, H-30), "✉️", font=FONT_TEXT)
        
        img.save(f"{output_dir}/frame_{frame_idx:06d}.png")
        frame_idx += 1
    
    return frame_idx

# === Phone Renderer ===

def render_frames_phone(messages, audio_files, output_dir, fps=30):
    """Render frames for phone call with proper timing."""
    os.makedirs(output_dir, exist_ok=True)
    
    W, H = 608, 1080
    
    timeline, total_dur = build_timeline(audio_files)
    
    # Incoming call (2s) then active call
    incoming_dur = 2.0
    
    goku_avatar = get_sprite("Goku")
    vegeta_avatar = get_sprite("Vegeta")
    
    total_frames = int((incoming_dur + total_dur + 1) * fps)
    
    frame_idx = 0
    for f in range(total_frames):
        t = f / fps
        
        img = Image.new("RGBA", (608, 1080), (26, 26, 46, 255))
        d = ImageDraw.Draw(img)
        
        if t < incoming_dur:
            # Incoming call screen
            if vegeta_avatar:
                av = make_avatar_circular(vegeta_avatar, 80)
                img.paste(av, (W//2-40, 180), av)
            d.text((W//2-35, 280), "Vegeta", fill=(255,255,255), font=FONT_TITLE)
            d.text((W//2-25, 320), "mobile", fill=(150,150,150), font=FONT_TEXT)
            d.text((W//2-70, H-120), "slide to answer", fill=(200,200,200), font=FONT_TEXT)
            
            # Accept/Decline
            accept = ASSETS["phone"].get("button_accept")
            decline = ASSETS["phone"].get("button_decline")
            if accept:
                img.paste(accept, (80, H-200), accept)
            if decline:
                img.paste(decline, (W-140, H-200), decline)
        else:
            # Active call
            call_t = t - incoming_dur
            
            # Small avatar
            if vegeta_avatar:
                av = make_avatar_circular(vegeta_avatar, 35)
                img.paste(av, (W//2-17, 60), av)
            d.text((W//2-25, 105), "Vegeta", fill=(255,255,255), font=FONT_NAME)
            
            # Timer
            mins = int(call_t) // 60
            secs = int(call_t) % 60
            d.text((W//2-20, 130), f"{mins}:{secs:02d}", fill=(200,200,200), font=FONT_TEXT)
            
            # Speaking indicator
            msg_idx = 0
            elapsed = 0
            for i, tl in enumerate(timeline):
                if elapsed <= call_t < elapsed + tl["duration"]:
                    msg_idx = i
                    break
                elapsed += tl["duration"]
            
            if msg_idx < len(messages):
                who = "Goku" if messages[msg_idx]["speaker"] == "Goku" else "Vegeta"
                d.text((W//2-35, 150), f"{who} talking", fill=(0,200,100), font=FONT_SMALL)
            
            # 6 buttons
            buttons = [
                ("speaker", W//2-90, H//2+30),
                ("mute", W//2+30, H//2+30),
                ("keypad", W//2-90, H//2+110),
                ("add_call", W//2+30, H//2+110),
                ("facetime", W//2-90, H//2+190),
                ("contacts", W//2+30, H//2+190)
            ]
            for name, bx, by in buttons:
                btn = ASSETS["phone"].get(f"button_{name}")
                if btn:
                    img.paste(btn, (bx, by), btn)
            
            # End call
            end = ASSETS["phone"].get("button_end")
            if end:
                img.paste(end, (W//2-30, H-80), end)
        
        img.save(f"{output_dir}/frame_{frame_idx:06d}.png")
        frame_idx += 1
    
    return frame_idx

if __name__ == "__main__":
    print("UI Renderer loaded. Use render_frames_whatsapp(), render_frames_x(), or render_frames_phone()")
