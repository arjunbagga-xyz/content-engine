"""Generate consistent avatar images for characters.

Uses character name hash to generate deterministic color + initial.
Same character = same avatar every time.
"""
import os
import hashlib
from PIL import Image, ImageDraw, ImageFont
from pathlib import Path

AVATAR_DIR = Path("D:/Open Projects/Content Engine/data/avatars")
AVATAR_SIZE = (256, 256)


def _name_to_color(name: str) -> tuple:
    """Generate consistent RGB color from name."""
    h = hashlib.md5(name.encode()).hexdigest()
    r = int(h[0:2], 16)
    g = int(h[2:4], 16)
    b = int(h[4:6], 16)
    return (r, g, b)


def _name_to_contrast_color(name: str) -> tuple:
    """Generate a contrasting color for text."""
    h = hashlib.md5((name + "contrast").encode()).hexdigest()
    r = int(h[6:8], 16)
    g = int(h[8:10], 16)
    b = int(h[10:12], 16)
    # Ensure it's bright enough
    r = max(r, 200)
    g = max(g, 200)
    b = max(b, 200)
    return (r, g, b)


def generate_avatar(name: str, initial: str = None) -> str:
    """Generate avatar image. Returns path to image."""
    AVATAR_DIR.mkdir(parents=True, exist_ok=True)
    
    path = AVATAR_DIR / f"{name.lower().replace(' ', '_')}.png"
    
    if path.exists():
        return str(path)
    
    bg_color = _name_to_color(name)
    text_color = _name_to_contrast_color(name)
    
    if initial is None:
        initial = name[0].upper() if name else "?"
    
    img = Image.new("RGB", AVATAR_SIZE, bg_color)
    draw = ImageDraw.Draw(img)
    
    # Draw a circle in the center
    circle_r = 80
    circle_x = AVATAR_SIZE[0] // 2
    circle_y = AVATAR_SIZE[1] // 2
    draw.ellipse(
        [circle_x - circle_r, circle_y - circle_r, 
         circle_x + circle_r, circle_y + circle_r],
        fill=text_color
    )
    
    # Draw initial
    try:
        font = ImageFont.truetype("arial.ttf", 72)
    except:
        font = ImageFont.load_default()
    
    bbox = draw.textbbox((0, 0), initial, font=font)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]
    text_x = circle_x - text_w // 2
    text_y = circle_y - text_h // 2 - 10
    
    draw.text((text_x, text_y), initial, fill=bg_color, font=font)
    
    img.save(str(path))
    return str(path)


def get_avatar(name: str, initial: str = None) -> str:
    """Get avatar path (generate if needed)."""
    path = AVATAR_DIR / f"{name.lower().replace(' ', '_')}.png"
    if path.exists():
        return str(path)
    return generate_avatar(name, initial)


if __name__ == "__main__":
    # Test
    names = ["Goku", "Vegeta", "Trump", "Elon", "Zuck", "Tate", "Peppa"]
    for n in names:
        p = generate_avatar(n)
        print(f"{n}: {p}")
