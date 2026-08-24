import os
import json
import random
import logging
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
from src.core.config import config

logger = logging.getLogger("content_engine.media_library")

class MediaLibrary:
    def __init__(self, account_id: str):
        self.account_id = account_id
        # Look up characters yaml to find paths
        # Find matching character config by matching its "id" field
        chars = config.load_characters()
        char_conf = {}
        for c_key, c_val in chars.items():
            if c_val.get("id") == account_id:
                char_conf = c_val
                break
        
        lib_conf = char_conf.get("media_library", {})
        self.library_path = Path(lib_conf.get("path", f"data/media_library/{account_id}"))
        self.manifest_path = Path(lib_conf.get("manifest", f"data/media_library/{account_id}/manifest.json"))
        self.gameplay_source = Path(lib_conf.get("gameplay_source", "data/media_library/gameplay_subway_surfers.mp4"))
        
        self.manifest = {"assets": []}
        self.load_manifest()

    def load_manifest(self):
        """Loads manifest.json from the media library path."""
        if self.manifest_path.exists():
            try:
                with open(self.manifest_path, "r", encoding="utf-8") as f:
                    self.manifest = json.load(f)
                logger.info(f"Loaded {len(self.manifest.get('assets', []))} assets from manifest for '{self.account_id}'")
            except Exception as e:
                logger.error(f"Error loading manifest for '{self.account_id}': {str(e)}")
        else:
            logger.warning(f"Manifest not found at {self.manifest_path}. Library is currently empty.")

    def save_manifest(self):
        """Saves current manifest state back to disk."""
        self.manifest_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with open(self.manifest_path, "w", encoding="utf-8") as f:
                json.dump(self.manifest, f, indent=2)
            logger.info(f"Saved manifest to {self.manifest_path}")
        except Exception as e:
            logger.error(f"Failed to save manifest: {str(e)}")

    def search_assets(self, tags: list, asset_type: str = None) -> list:
        """Searches assets by tags and optional asset type (clip, image, reaction, audio)."""
        matched = []
        tags_lower = [t.lower() for t in tags]
        
        for asset in self.manifest.get("assets", []):
            if asset_type and asset.get("type") != asset_type:
                continue
                
            asset_tags = [t.lower() for t in asset.get("tags", [])]
            # Calculate match score (number of matching tags)
            score = sum(1 for t in tags_lower if t in asset_tags)
            
            # Or match via filename / description
            desc = asset.get("description", "").lower()
            filename = asset.get("filename", "").lower()
            for t in tags_lower:
                if t in desc or t in filename:
                    score += 1

            if score > 0:
                matched.append((asset, score))

        # Sort by match score descending
        matched.sort(key=lambda x: x[1], reverse=True)
        return [item[0] for item in matched]

    def get_random_asset(self, asset_type: str = None) -> dict:
        """Returns a random asset of a specific type."""
        assets = self.manifest.get("assets", [])
        if asset_type:
            assets = [a for a in assets if a.get("type") == asset_type]
            
        if not assets:
            return None
        return random.choice(assets)

    def select_best_assets(self, script_keywords: list, count: int = 3, asset_type: str = "clip") -> list:
        """Selects the best count assets matching script keywords, falling back to random if needed."""
        matches = self.search_assets(script_keywords, asset_type=asset_type)
        selected = []
        
        # Take unique matches
        for m in matches:
            if m["filename"] not in [s["filename"] for s in selected]:
                selected.append(m)
            if len(selected) >= count:
                break
                
        # Fill remaining slots with random assets of same type
        while len(selected) < count:
            rand_asset = self.get_random_asset(asset_type=asset_type)
            if not rand_asset:
                break
            if rand_asset["filename"] not in [s["filename"] for s in selected]:
                selected.append(rand_asset)
            else:
                # If we've run out of unique assets, just allow duplicate or break
                if len(selected) >= len(self.manifest.get("assets", [])):
                    selected.append(rand_asset)
                    break
                
        return selected[:count]

class MediaLibraryCompositor:
    @staticmethod
    def generate_meme(background_image_path: str, text: str, output_path: str, watermark: str = None) -> str:
        """
        Creates a high-quality, modern Twitter/threads style meme card.
        Has a white canvas, meme text at the top, and the screenshot centered below it.
        """
        logger.info(f"Composing meme from background: {background_image_path}")
        
        try:
            bg_img = Image.open(background_image_path)
        except Exception as e:
            logger.error(f"Failed to open background image: {str(e)}")
            # Create a blank fallback image
            bg_img = Image.new("RGB", (800, 600), "#222")

        # 1080x1350 is a very premium portrait aspect ratio for Instagram
        canvas_width = 1080
        canvas_height = 1350
        
        # Create premium off-white canvas
        canvas = Image.new("RGB", (canvas_width, canvas_height), "#ffffff")
        draw = ImageDraw.Draw(canvas)

        # We try to use a standard system font
        font_paths = [
            "C:/Windows/Fonts/segoeuib.ttf",  # Segoe UI Bold
            "C:/Windows/Fonts/arialbd.ttf",   # Arial Bold
            "arial.ttf"
        ]
        
        font = None
        for path in font_paths:
            try:
                font = ImageFont.truetype(path, 42)
                break
            except IOError:
                continue
        if not font:
            font = ImageFont.load_default()

        # Text wrapping helper
        def wrap_text(txt, max_w):
            words = txt.split()
            lines = []
            current = []
            for w in words:
                test = " ".join(current + [w])
                bbox = draw.textbbox((0, 0), test, font=font)
                if bbox[2] - bbox[0] <= max_w:
                    current.append(w)
                else:
                    lines.append(" ".join(current))
                    current = [w]
            if current:
                lines.append(" ".join(current))
            return lines

        lines = wrap_text(text, 960)
        
        # Draw text at the top
        y_text = 80
        line_height = 55
        for line in lines:
            bbox = draw.textbbox((0, 0), line, font=font)
            # Center-align text
            x_text = (canvas_width - (bbox[2] - bbox[0])) // 2
            draw.text((x_text, y_text), line, font=font, fill="#1a1a1a")
            y_text += line_height

        # Reserve top portion for text: y_text now marks the end of text area
        # Calculate available height for the image layer
        img_top = y_text + 40
        img_bottom = canvas_height - 120
        available_h = img_bottom - img_top
        available_w = 960 # Margins of 60px on each side

        # Resize image to fit nicely within available box while preserving aspect ratio
        img_w, img_h = bg_img.size
        aspect = img_w / img_h
        
        new_w = available_w
        new_h = int(new_w / aspect)
        
        if new_h > available_h:
            new_h = available_h
            new_w = int(new_h * aspect)

        bg_resized = bg_img.resize((new_w, new_h), Image.Resampling.LANCZOS)
        
        # Paste centered image
        x_offset = (canvas_width - new_w) // 2
        y_offset = img_top + (available_h - new_h) // 2
        canvas.paste(bg_resized, (x_offset, y_offset))

        # Draw a beautiful thin gray border around the screenshot to make it feel premium
        draw.rectangle([x_offset, y_offset, x_offset + new_w, y_offset + new_h], outline="#e1e8ed", width=2)

        # Draw watermark at the bottom
        if watermark:
            try:
                watermark_font = ImageFont.truetype("C:/Windows/Fonts/arial.ttf", 26)
            except IOError:
                watermark_font = ImageFont.load_default()
            w_bbox = draw.textbbox((0, 0), watermark, font=watermark_font)
            w_x = (canvas_width - (w_bbox[2] - w_bbox[0])) // 2
            draw.text((w_x, canvas_height - 70), watermark, font=watermark_font, fill="#8899a6")

        canvas.save(output_path)
        logger.info(f"Meme generated and saved successfully to {output_path}")
        return output_path

class ManifestBuilder:
    @staticmethod
    def auto_scan_and_build(library_dir: Path, output_manifest_path: Path) -> dict:
        """
        Scans a directory structure and automatically builds/updates a manifest.json.
        Types:
        - clips/ -> clip
        - images/ -> image
        - reactions/ -> reaction
        - audio/ -> audio
        """
        logger.info(f"Scanning media library directory: {library_dir}")
        library_dir.mkdir(parents=True, exist_ok=True)
        
        existing_assets = {}
        if output_manifest_path.exists():
            try:
                with open(output_manifest_path, "r", encoding="utf-8") as f:
                    old_manifest = json.load(f)
                    for a in old_manifest.get("assets", []):
                        existing_assets[a["filename"]] = a
            except Exception:
                pass

        new_assets = []
        
        # Direct folders mapping
        folders_mapping = {
            "clips": "clip",
            "images": "image",
            "reactions": "reaction",
            "audio": "audio"
        }

        for folder, asset_type in folders_mapping.items():
            folder_path = library_dir / folder
            folder_path.mkdir(exist_ok=True)
            
            # Scan files
            for file in folder_path.iterdir():
                if file.is_file():
                    rel_path = f"{folder}/{file.name}"
                    
                    # If already exists in manifest, keep its tags/metadata
                    if rel_path in existing_assets:
                        new_assets.append(existing_assets[rel_path])
                    else:
                        # Otherwise build baseline meta
                        base_tags = [folder, file.stem.lower().replace("_", " ").replace("-", " ")]
                        # Split by space
                        split_tags = []
                        for tag in base_tags:
                            split_tags.extend(tag.split())
                        # Unique tags
                        unique_tags = list(set([t for t in split_tags if len(t) > 2]))
                        
                        asset_entry = {
                            "filename": rel_path,
                            "type": asset_type,
                            "tags": unique_tags,
                            "description": f"{asset_type.capitalize()} asset from {file.stem}"
                        }
                        
                        # Add specific duration field for clips/audio
                        if asset_type in ("clip", "audio"):
                            asset_entry["duration_s"] = 10.0 # Default fallback
                            
                        new_assets.append(asset_entry)

        manifest_data = {"assets": new_assets}
        output_manifest_path.parent.mkdir(parents=True, exist_ok=True)
        
        with open(output_manifest_path, "w", encoding="utf-8") as f:
            json.dump(manifest_data, f, indent=2)
            
        logger.info(f"Auto-scanned and built manifest at {output_manifest_path} with {len(new_assets)} assets.")
        return manifest_data
