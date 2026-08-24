import logging
import requests
import urllib.parse
import asyncio
from PIL import Image, ImageDraw, ImageFont, ImageFilter
from pathlib import Path
from src.core.config import config

logger = logging.getLogger("content_engine.image_generator")

class ImageGenerator:
    @staticmethod
    async def generate_quote_card(text: str, character_id: str, output_path: str) -> str:
        """Generates a highly-stylized branded quote/thought card locally using Pillow.
        Completely free, no GPU or API key required.
        """
        logger.info(f"Generating quote card for {character_id}...")
        
        # Color schemes & fonts based on character YAML identities
        styles = {
            "maya_tech": {
                "bg": "#0f0f1b",          # Deep space / tech dark
                "text": "#00f0ff",        # Cyber cyan
                "border": "#ff007f",      # Neon pink accent
                "secondary": "#ffffff",
                "font_size": 48
            },
            "luna_art": {
                "bg": "#fcf8f2",          # Cozy cream
                "text": "#503e2c",        # Warm espresso
                "border": "#c9ada7",      # Dusty rose accent
                "secondary": "#8a7a6b",
                "font_size": 44
            },
            "chloe_fit": {
                "bg": "#eff6f0",          # Organic sage/mint
                "text": "#2d3a32",        # Slate forest green
                "border": "#b5c9b8",      # Moss green accent
                "secondary": "#576d5e",
                "font_size": 46
            },
            "anime_sensei": {
                "bg": "#181825",          # Cozy dark slate blue (Catppuccin Mocha Crust/Base)
                "text": "#cdd6f4",        # Pastel off-white
                "border": "#cba6f7",      # Soft lavender accent
                "secondary": "#a6adc8",
                "font_size": 44
            },
            "code_mysteries": {
                "bg": "#05080c",          # Deep matrix dark
                "text": "#00ff66",        # Glowing terminal green
                "border": "#005500",      # Dark forest green border
                "secondary": "#88aa88",
                "font_size": 42
            }
        }
        
        style = styles.get(character_id, {
            "bg": "#121212", "text": "#ffffff", "border": "#333333", "secondary": "#aaaaaa", "font_size": 40
        })

        # Create a blank square canvas (1080x1080 is perfect for IG grid)
        img = Image.new("RGB", (1080, 1080), style["bg"])
        draw = ImageDraw.Draw(img)
        
        # Draw a sleek neon/minimalist border
        draw.rectangle([40, 40, 1040, 1040], outline=style["border"], width=6)
        
        # Text wrapping helper
        def wrap_text(text, font, max_width):
            words = text.split()
            lines = []
            current_line = []
            for word in words:
                test_line = " ".join(current_line + [word])
                bbox = draw.textbbox((0, 0), test_line, font=font)
                if bbox[2] - bbox[0] <= max_width:
                    current_line.append(word)
                else:
                    lines.append(" ".join(current_line))
                    current_line = [word]
            if current_line:
                lines.append(" ".join(current_line))
            return lines

        # We try to use a standard system font, otherwise fall back to default
        font_paths = [
            "C:/Windows/Fonts/consola.ttf" if character_id == "maya_tech" else "C:/Windows/Fonts/georgia.ttf",
            "C:/Windows/Fonts/arial.ttf",
            "consola.ttf",
            "arial.ttf"
        ]
        
        font = None
        for path in font_paths:
            try:
                font = ImageFont.truetype(path, style["font_size"])
                break
            except IOError:
                continue
                
        if not font:
            font = ImageFont.load_default()

        # Wrap and center text
        lines = wrap_text(text, font, 900)
        total_height = sum(draw.textbbox((0, 0), line, font=font)[3] - draw.textbbox((0, 0), line, font=font)[1] for line in lines)
        # Add spacing between lines
        line_spacing = 20
        total_height += line_spacing * (len(lines) - 1)
        
        y = (1080 - total_height) // 2
        for line in lines:
            bbox = draw.textbbox((0, 0), line, font=font)
            x = (1080 - (bbox[2] - bbox[0])) // 2
            draw.text((x, y), line, font=font, fill=style["text"])
            y += (bbox[3] - bbox[1]) + line_spacing

        # Draw a cute footer tag
        footer_text = f"@{character_id.replace('_', '.')}"
        try:
            footer_font = ImageFont.truetype(font_paths[-2], 24)
        except IOError:
            footer_font = ImageFont.load_default()
            
        footer_bbox = draw.textbbox((0, 0), footer_text, font=footer_font)
        footer_x = (1080 - (footer_bbox[2] - footer_bbox[0])) // 2
        draw.text((footer_x, 960), footer_text, font=footer_font, fill=style["secondary"])
        
        # Save image
        img.save(output_path)
        logger.info(f"Quote card successfully saved to {output_path}")
        return output_path

    @staticmethod
    async def fetch_pexels_stock_photo(query: str, output_path: str, is_fallback: bool = False) -> str:
        """Downloads a relevant, high-resolution aesthetic stock photo from Pexels API."""
        if not config.PEXELS_API_KEY:
            raise ValueError("Pexels API key not configured in .env")

        logger.info(f"Searching Pexels for stock photo query: '{query}'...")
        url = f"https://api.pexels.com/v1/search?query={urllib.parse.quote(query)}&per_page=1&orientation=portrait"
        headers = {"Authorization": config.PEXELS_API_KEY}
        
        response = requests.get(url, headers=headers, timeout=20)
        if response.status_code != 200:
            raise RuntimeError(f"Pexels API error ({response.status_code}): {response.text}")
            
        data = response.json()
        if not data.get("photos"):
            if is_fallback:
                raise FileNotFoundError(f"No stock photos found, even for fallback query: '{query}'")
            logger.warning(f"No stock photos found for '{query}'. Falling back to abstract aesthetic.")
            return await ImageGenerator.fetch_pexels_stock_photo("abstract aesthetic", output_path, is_fallback=True)

        photo_url = data["photos"][0]["src"]["large2x"]
        logger.info(f"Downloading Pexels photo from: {photo_url}")
        
        photo_response = requests.get(photo_url, timeout=30)
        with open(output_path, "wb") as f:
            f.write(photo_response.content)
            
        logger.info(f"Stock photo saved to {output_path}")
        return output_path

    @staticmethod
    async def generate_ai_character_image(prompt: str, output_path: str, seed: int = None, negative_prompt: str = None) -> str:
        """Generates a high-quality character image using Pollinations.ai (zero-cost FLUX API).
        
        A fixed `seed` makes the same character render the SAME person across every post,
        which is the key to visual consistency on the free tier (no LoRA available).
        """
        import time
        logger.info(f"Calling Pollinations FLUX engine with prompt: '{prompt[:90]}...'")
        encoded_prompt = urllib.parse.quote(prompt)
        # Append negative prompt to the positive prompt (Pollinations has no separate neg param)
        if negative_prompt:
            full = f"{prompt}. --no {negative_prompt}"
        else:
            full = prompt
        encoded_full = urllib.parse.quote(full)
        # Fixed seed => consistent identity. model=flux-realism is the photoreal FLUX
        # model (avoids the anime/CGI "AI slop" look of plain flux).
        model = "flux-realism"
        seed_part = f"&seed={seed}" if seed is not None else ""
        url = f"https://image.pollinations.ai/prompt/{encoded_full}?width=1024&height=1024&nologo=true&model={model}{seed_part}"
        
        max_retries = 3
        last_error = None
        
        for attempt in range(1, max_retries + 1):
            try:
                loop = asyncio.get_event_loop()
                # Run the blocking request in a thread pool executor
                response = await loop.run_in_executor(None, lambda: requests.get(url, timeout=30))
                
                if response.status_code == 200 and len(response.content) > 2000:
                    with open(output_path, "wb") as f:
                        f.write(response.content)
                    logger.info(f"AI character image successfully generated and saved to {output_path} (attempt {attempt}/{max_retries})")
                    return output_path
                else:
                    logger.warning(f"Pollinations API returned status {response.status_code} on attempt {attempt}. Retrying...")
                    last_error = f"Status {response.status_code}: {response.text[:200]}"
            except Exception as e:
                logger.warning(f"Pollinations call failed on attempt {attempt}: {str(e)}. Retrying...")
                last_error = str(e)
            
            if attempt < max_retries:
                await asyncio.sleep(3) # Short cooldown on failure
                
        raise RuntimeError(f"All Pollinations API retries failed. Last error: {last_error}")

    @staticmethod
    async def generate_character_portrait(character_config: dict, base_prompt: str, output_path: str) -> str:
        """Generates a consistent character image using the LoRA router."""
        from src.generation.consistency import LoRAInferenceRouter
        return await LoRAInferenceRouter.generate_image(character_config, base_prompt, output_path)

    @staticmethod
    async def generate_faceless_static(character_config: dict, text: str, output_path: str) -> str:
        """
        Generates a static post for a faceless account by overlaying text onto a curated image asset.
        """
        char_id = character_config.get("id")
        logger.info(f"Generating faceless static post for {char_id}...")
        
        from src.generation.media_library import MediaLibrary, MediaLibraryCompositor
        lib = MediaLibrary(char_id)
        
        # Tokenize the script text to extract search tags
        import re
        words = re.findall(r'\b\w+\b', text.lower())
        tags = [w for w in words if len(w) > 3]
        
        # Search the library for matching image assets
        assets = lib.select_best_assets(tags, count=1, asset_type="image")
        if not assets:
            assets = lib.select_best_assets(tags, count=1, asset_type="reaction")
            
        if assets:
            asset = assets[0]
            bg_path = lib.library_path / asset["filename"]
            logger.info(f"Selected library image: {bg_path}")
        else:
            # Fallback: if library contains no images at all, generate a beautiful quote card
            logger.warning(f"No library images found for {char_id}. Generating fallback stylized quote card.")
            return await ImageGenerator.generate_quote_card(text, char_id, output_path)

        # Compile modern meme format
        watermark = f"@{character_config.get('name', char_id)}"
        return MediaLibraryCompositor.generate_meme(str(bg_path), text, output_path, watermark=watermark)
