import os
import random
import logging
import requests
import asyncio
import subprocess
import urllib.parse
from typing import List, Dict, Any
from pathlib import Path
from src.core.config import config

logger = logging.getLogger("content_engine.video_generator")


# --- Hardware encoder selection (NVENC on the 1650, CPU fallback otherwise) ---
# NVENC is a dedicated encoder block on the GTX 1650, independent of its CUDA
# cores. It encodes 1080x1920 H.264 at ~10x realtime with ~zero CPU load. We
# probe ffmpeg once for the h264_nvenc encoder; if absent we fall back to the
# old software libx264 (slow but always available).
_NVENC_OK = None  # None = not probed yet


def _use_nvenc() -> bool:
    global _NVENC_OK
    if _NVENC_OK is None:
        try:
            out = subprocess.run(
                ["ffmpeg", "-hide_banner", "-encoders"],
                capture_output=True, text=True, timeout=30,
            ).stdout
            _NVENC_OK = "h264_nvenc" in out
        except Exception:
            _NVENC_OK = False
        if _NVENC_OK:
            logger.info("video: h264_nvenc available -> using NVENC hardware encode")
        else:
            logger.info("video: h264_nvenc NOT available -> falling back to libx264")
    return _NVENC_OK


def vcodec_args() -> list:
    """Return the -c:v ... args for an ffmpeg encode, preferring NVENC."""
    if _use_nvenc():
        # -cq 23 ~= libx264 -crf 26 visually; -preset p1 = fastest.
        # -tune ull (ultra-low-latency) skips lookahead for speed.
        return ["-c:v", "h264_nvenc", "-preset", "p1", "-tune", "ull", "-cq", "23"]
    return ["-c:v", "libx264", "-preset", "ultrafast", "-crf", "26"]


def _character_key_of(sprite_path: str) -> str:
    """Derive the character key from a sprite path under data/characters/<char>/sprites/."""
    from pathlib import Path
    p = Path(sprite_path)
    # Look for the 'characters' segment, the next segment is the character key.
    parts = p.parts
    if "characters" in parts:
        i = parts.index("characters")
        if i + 1 < len(parts):
            return parts[i + 1]
    return None


# Emotion -> ffmpeg color/transform grade applied to the composited frame.
# Each returns an ffmpeg filter fragment (applied after [stacked]).
_GRADE_FILTERS = {
    "none": "",
    "red_shake": "noise=alls=6:allf=t+u,eq=contrast=1.15:saturation=1.4:gamma_r=1.2",
    "warm_glow": "eq=saturation=1.25:gamma_r=1.1:gamma_b=0.92:brightness=0.03",
    "desaturate": "hue=s=0.35,eq=brightness=-0.02",
    "gold_tint": "eq=saturation=1.2:gamma_r=1.18:gamma_g=1.05:gamma_b=0.8",
    "cyan_tint": "eq=saturation=1.15:gamma_r=0.85:gamma_g=1.05:gamma_b=1.2",
    "flash": "eq=brightness=0.08:contrast=1.2,fade=in:0:8",
    "soft_vignette": "vignette=PI/4",
    "dark_red": "eq=saturation=1.3:gamma_r=1.3:gamma_g=0.8:gamma_b=0.8:brightness=-0.04",
    "amber_boost": "eq=saturation=1.3:gamma_r=1.2:gamma_g=1.05:gamma_b=0.7",
    "pulse": "zoompan=z='min(zoom+0.0008,1.04)':d=1:s=1080x1920:fps=30,eq=saturation=1.15",
}


def _grade_filter(grade: str) -> str:
    return _GRADE_FILTERS.get(grade, "")

class VideoGenerator:
    @staticmethod
    async def fetch_pexels_stock_videos(query: str, max_duration: float = 30.0) -> List[str]:
        """Searches and downloads high-quality vertical B-roll stock video clips from Pexels API.
        
        Returns:
            List of local video clip paths.
        """
        if not config.PEXELS_API_KEY:
            raise ValueError("Pexels API key not configured in .env")

        logger.info(f"Searching Pexels for stock videos matching query: '{query}'...")
        url = f"https://api.pexels.com/videos/search?query={urllib.parse.quote(query)}&per_page=5&orientation=portrait"
        headers = {"Authorization": config.PEXELS_API_KEY}
        
        response = requests.get(url, headers=headers, timeout=20)
        if response.status_code != 200:
            raise RuntimeError(f"Pexels API video search failed: {response.text}")
            
        data = response.json()
        if not data.get("videos"):
            logger.warning(f"No vertical stock videos found for '{query}'. Trying 'aesthetic space'...")
            return await VideoGenerator.fetch_pexels_stock_videos("aesthetic space", max_duration)

        downloaded_paths = []
        duration_downloaded = 0.0
        
        for idx, video in enumerate(data["videos"]):
            if duration_downloaded >= max_duration:
                break
                
            # Pick a suitable resolution vertical file (usually HD file)
            files = video.get("video_files", [])
            vertical_files = [f for f in files if f.get("width") and f.get("height") and f["height"] > f["width"]]
            
            if not vertical_files:
                if not files:
                    logger.warning(f"No video files found for video {video.get('id')}. Skipping.")
                    continue
                # Fallback to first file
                file_url = files[0]["link"]
            else:
                # Sort to get a reasonable HD file (~720x1280 is fast to render)
                vertical_files.sort(key=lambda f: abs(f.get("width", 0) - 720))
                file_url = vertical_files[0]["link"]

            output_clip_path = str(config.OUTPUTS_DIR / f"pexels_clip_{idx}_{video['id']}.mp4")
            logger.info(f"Downloading Pexels clip from: {file_url}")
            
            clip_res = requests.get(file_url, timeout=60)
            with open(output_clip_path, "wb") as f:
                f.write(clip_res.content)
                
            downloaded_paths.append(output_clip_path)
            duration_downloaded += video.get("duration", 10.0)
            
        logger.info(f"Successfully downloaded {len(downloaded_paths)} clips (Total dur: {duration_downloaded}s).")
        return downloaded_paths

    @staticmethod
    async def get_gameplay_clip() -> str:
        """Finds and returns a random pre-recorded gameplay clip (e.g. Subway Surfers / Minecraft).
        If the directory is empty or doesn't exist, automatically falls back to Pexels stock video downloads!
        """
        gameplay_dir = config.DATA_DIR / "gameplay"
        gameplay_dir.mkdir(exist_ok=True)
        
        clips = list(gameplay_dir.glob("*.mp4"))
        if not clips:
            logger.info("No local gameplay clips found in data/gameplay. Downloading vertical stock B-roll...")
            # Download a cool abstract/glitch/gaming aesthetic clip as fallback
            downloaded = await VideoGenerator.fetch_pexels_stock_videos("cyberpunk gaming B-roll", max_duration=30.0)
            if not downloaded:
                raise FileNotFoundError("Could not locate local gameplay clips or stock fallback clips.")
            return downloaded[0]
            
        selected_clip = str(random.choice(clips))
        logger.info(f"Selected pre-recorded gameplay clip: {selected_clip}")
        return selected_clip

    @staticmethod
    async def transcribe_audio_deepgram(audio_path: str) -> List[Dict[str, Any]]:
        """Transcribes TTS audio using Deepgram API to get highly-precise word-level timestamps.
        
        Returns:
            List of dicts containing: {'word': str, 'start': float, 'end': float}
        """
        if not config.DEEPGRAM_API_KEY:
            raise ValueError("Deepgram API key not configured in .env")

        logger.info(f"Transcribing audio {audio_path} using Deepgram...")
        url = "https://api.deepgram.com/v1/listen?smart_format=true&model=nova-2"
        headers = {
            "Authorization": f"Token {config.DEEPGRAM_API_KEY}",
            "Content-Type": "audio/wav"
        }
        
        last_err = None
        for attempt in range(3):
            try:
                with open(audio_path, "rb") as audio_file:
                    response = requests.post(url, headers=headers, data=audio_file, timeout=30)
                if response.status_code == 429 or response.status_code >= 500:
                    # rate-limited or transient server error: back off and retry
                    await asyncio.sleep(1.5 * (attempt + 1))
                    last_err = RuntimeError(f"Deepgram {response.status_code}")
                    continue
                if response.status_code != 200:
                    raise RuntimeError(f"Deepgram transcription failed ({response.status_code}): {response.text}")
                break
            except Exception as e:
                last_err = e
                await asyncio.sleep(1.0 * (attempt + 1))
                continue
        else:
            raise last_err or RuntimeError("Deepgram transcription failed after retries")

        data = response.json()
        try:
            words = data["results"]["channels"][0]["alternatives"][0]["words"]
            word_timestamps = [
                {
                    "word": w["word"],
                    "start": w["start"],
                    "end": w["end"]
                }
                for w in words
            ]
            logger.info(f"Transcribed {len(word_timestamps)} words successfully.")
            return word_timestamps
        except (KeyError, IndexError) as e:
            raise RuntimeError(f"Unexpected response structure from Deepgram API: {data}")

    @staticmethod
    def group_words(words: List[Dict[str, Any]], max_words: int = 4, max_gap: float = 0.5) -> List[Dict[str, Any]]:
        """Merge word-level timestamps into caption chunks so subtitles don't flash one word
        at a time. Groups consecutive words (gap <= max_gap) up to max_words per line."""
        if not words:
            return []
        chunks = []
        cur = [words[0]]
        for w in words[1:]:
            gap = w["start"] - cur[-1]["end"]
            if gap > max_gap or len(cur) >= max_words:
                chunks.append(cur)
                cur = [w]
            else:
                cur.append(w)
        if cur:
            chunks.append(cur)
        out = []
        for ch in chunks:
            out.append({
                "text": " ".join(x["word"] for x in ch),
                "start": ch[0]["start"],
                "end": ch[-1]["end"],
            })
        return out

    @staticmethod
    def generate_ass_subtitles(words: List[Dict[str, Any]], output_ass_path: str, grouped: bool = True, alignment: int = 8, character_key: str = None):
        """Generates an Advanced SubStation Alpha (.ass) file for faceless reels.

        By default `grouped=True` merges word-level timestamps into caption chunks (2-4 words)
        held for the whole phrase, instead of flashing one word at a time.
        `alignment`: 7=top-left, 8=top-center, 9=top-right (used for the reel caption band).

        Style: character-colored FILL, dark outline + shadow (no backing box).
        Top-center (Alignment=8) with a top MarginV so captions sit clear of the top edge.
        """
        # Character -> subtitle color (ASS &H00BBGGRR). Applied as the text FILL
        # (PrimaryColour) so the character color is always visible; a dark outline +
        # shadow keeps it readable on varied gameplay backgrounds. No backing box.
        CHAR_FILL = {
            "goku":    "&H00008CFF",  # orange (his gi)
            "vegeta":  "&H00FF8000",  # blue (his suit)
            "gohan":   "&H00C080FF",  # teal/purple
            "peter":   "&H0000A000",  # green
            "stewie":  "&H000000FF",  # red
            "meg":     "&H00C080C0",  # pink
        }
        fill = CHAR_FILL.get(character_key, "&H0000A5FF")  # default amber-ish
        # Dark outline + shadow for legibility on any background.
        outline = "&H00000000"  # near-black outline

        # Formats the timestamps to ASS time format: H:MM:SS.cs
        def format_time(seconds: float) -> str:
            h = int(seconds // 3600)
            m = int((seconds % 3600) // 60)
            s = int(seconds % 60)
            cs = int(round((seconds % 1) * 100))
            if cs == 100:
                s += 1
                cs = 0
            return f"{h}:{m:02d}:{s:02d}.{cs:02d}"

        ass_header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,Arial,72,{fill},&H000000FF,{outline},&H00000000,1,0,0,0,100,100,1,0,1,6,3,8,40,40,480,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
        dialogues = []
        if grouped:
            chunks = VideoGenerator.group_words(words)
            for ch in chunks:
                dialogues.append(
                    f"Dialogue: 0,{format_time(ch['start'])},{format_time(ch['end'])},Cap,,0,0,0,,{ch['text'].upper()}"
                )
        else:
            for w in words:
                dialogues.append(
                    f"Dialogue: 0,{format_time(w['start'])},{format_time(w['end'])},Cap,,0,0,0,,{w['word'].upper()}"
                )

        with open(output_ass_path, "w", encoding="utf-8") as f:
            f.write(ass_header)
            f.write("\n".join(dialogues))
            f.write("\n")

        logger.info(f"ASS subtitles saved to {output_ass_path} (char={character_key}, outline={outline})")

    @staticmethod
    async def compose_reel(background_video_path: str, audio_path: str, words: List[Dict[str, Any]], output_mp4_path: str, character_key: str = None) -> str:
        """Stitches and crops video, generates subtitles, and renders the final reel using FFmpeg.
        Runs fully on CPU. Takes ~15-45 seconds for a 60s video!
        """
        ass_path = output_mp4_path.replace(".mp4", ".ass")
        VideoGenerator.generate_ass_subtitles(words, ass_path, character_key=character_key)
        
        # Escape path backslashes for FFmpeg subtitle filter (especially critical on Windows!)
        escaped_ass_path = ass_path.replace("\\", "/").replace(":", "\\:")
        
        logger.info("Composing final vertical video with FFmpeg...")
        
        # Build command:
        is_image = background_video_path.lower().endswith(('.png', '.jpg', '.jpeg', '.webp'))
        
        # Subtitle-burned variant (try first)
        vfilter_sub = f"[0:v]scale=w=1080:h=1920:force_original_aspect_ratio=increase,crop=1080:1920,subtitles='{escaped_ass_path}'[v]"
        sub_cmd = ["ffmpeg", "-y"]
        if is_image:
            sub_cmd.extend(["-loop", "1", "-i", background_video_path])
        else:
            sub_cmd.extend(["-stream_loop", "-1", "-i", background_video_path])
        sub_cmd.extend([
            "-i", audio_path,
            "-filter_complex", vfilter_sub,
            "-map", "[v]", "-map", "1:a",
            *vcodec_args(),
            "-c:a", "aac", "-b:a", "128k",
            "-shortest",
            output_mp4_path
        ])
        # No-subtitle fallback variant (captions are nice-to-have, video must exist)
        nosub_cmd = ["ffmpeg", "-y"]
        if is_image:
            nosub_cmd.extend(["-loop", "1", "-i", background_video_path])
        else:
            nosub_cmd.extend(["-stream_loop", "-1", "-i", background_video_path])
        nosub_cmd.extend([
            "-i", audio_path,
            "-vf", "scale=w=1080:h=1920:force_original_aspect_ratio=increase,crop=1080:1920",
            *vcodec_args(),
            "-c:a", "aac", "-b:a", "128k",
            "-shortest",
            output_mp4_path
        ])

        def _run(cmd):
            loop = asyncio.get_event_loop()
            return loop.run_in_executor(None, lambda: subprocess.run(cmd, capture_output=True, text=True))
        
        logger.info(f"Executing FFmpeg Character Reel (with subtitles): {' '.join(sub_cmd)}")
        proc = await _run(sub_cmd)
        if proc.returncode != 0 or not os.path.exists(output_mp4_path) or os.path.getsize(output_mp4_path) < 1024:
            logger.warning(f"FFmpeg subtitle-burn failed (code {proc.returncode}); retrying without subtitles. stderr: {proc.stderr[:300]}")
            proc = await _run(nosub_cmd)
            if proc.returncode != 0:
                logger.error(f"FFmpeg failed (code {proc.returncode}): {proc.stderr}")
                raise RuntimeError(f"FFmpeg render failed: {proc.stderr}")
                
        logger.info(f"Reel composed successfully saved to: {output_mp4_path}")
        return output_mp4_path

    @staticmethod
    async def compose_sprite_reel(sprite_path: str, gameplay_video_path: str, audio_path: str, words: List[Dict[str, Any]], output_mp4_path: str, sprite_scale: float = 0.35, anchor: str = "center", grade: str = "none") -> str:
        """
        Faceless 'react' format: a transparent character SPRITE is overlaid onto full-frame
        gameplay footage (bottom-center by default), with word-by-word subtitles burned on top.
        Used by dbz_verse / familyguy_verse style accounts.
        The sprite is pre-scaled with PIL (robust against ffmpeg filter-var quirks), then
        composited via ffmpeg 'overlay'.
        """
        import asyncio as _asyncio
        from PIL import Image
        # Write the ASS to a SPACE-FREE temp dir and reference it by basename. ffmpeg's
        # libass `subtitles` filter cannot resolve Windows paths that contain a drive colon
        # and/or a space (e.g. "D:\Open Projects\...\x.ass") — it silently drops the track.
        # Running ffmpeg with cwd=<tempdir> and a relative filename is the robust fix.
        import tempfile as _tempfile
        _ass_basename = f"_sub_{abs(hash(output_mp4_path))}_{os.getpid()}.ass"
        ass_path = os.path.join(_tempfile.gettempdir(), _ass_basename)
        VideoGenerator.generate_ass_subtitles(words, ass_path, character_key=_character_key_of(sprite_path))
        escaped_ass_path = _ass_basename  # relative to cwd below

        logger.info(f"Composing sprite-reel: sprite={sprite_path}, gameplay={gameplay_video_path}")

        # Sprites are already clean transparent PNGs (no white-box backgrounds), so we do
        # NOT chroma-key white pixels — that was punching holes in legit white armor/gi.
        # Just premultiply (so RGB is black where alpha=0) and scale to a fraction of frame
        # height. Default 0.35 (35% of frame height) keeps the character present but not
        # dominating. A small safety margin prevents wide/tall sprites from clipping edges.
        try:
            sp_img = Image.open(sprite_path).convert("RGBA")
            # Premultiply: composite onto fresh transparent canvas so RGB is black where alpha=0
            transparent = Image.new("RGBA", sp_img.size, (0, 0, 0, 0))
            sp_img = Image.alpha_composite(transparent, sp_img)
            # Target height as a fraction of 1920 (reel height); keep aspect.
            target_h = int(1920 * sprite_scale)
            max_h = int(1920 * 0.7)
            if target_h > max_h:
                target_h = max_h
            ratio = target_h / sp_img.height
            target_w = int(sp_img.width * ratio)
            sp_img = sp_img.resize((target_w, target_h), Image.Resampling.LANCZOS)
            tmp_sprite = str(Path(output_mp4_path).with_suffix("")) + "_sprite_overlay.png"
            # Keep the sprite in the space-free temp dir too: ffmpeg's libavformat struggles
            # to resolve absolute Windows paths (drive colon + space) when cwd is changed,
            # so ALL per-render temp artifacts live in tempdir and are referenced relatively.
            tmp_sprite = os.path.join(_tempfile.gettempdir(),
                                      f"_spr_{abs(hash(output_mp4_path))}_{os.getpid()}.png")
            sp_img.save(tmp_sprite)
        except Exception as e:
            logger.error(f"Sprite pre-scale failed: {e}")
            raise RuntimeError(f"Sprite pre-scale failed: {e}")

        # Anchor: center | bl | br — bottom-anchored, safe without clamp (sprite capped at 70% H)
        if anchor == "bl":
            x_pos, y_pos = "40", "H-overlay_h-40"
        elif anchor == "br":
            x_pos, y_pos = "W-overlay_w-40", "H-overlay_h-40"
        else:  # center, bottom-anchored with margin
            x_pos, y_pos = "(W-overlay_w)/2", "H-overlay_h-80"

        grade_filter = _grade_filter(grade)
        # Normalize the gameplay clip to the reel canvas (1080x1920) BEFORE overlaying the
        # sprite. The sprite is scaled against a 1920px-frame assumption (target_h = 1920 *
        # sprite_scale), so if the source clip isn't 1080x1920 the sprite overflows/clips.
        # Normalizing first makes placement deterministic regardless of which gameplay
        # clip was randomly selected (e.g. subway 288x640, beat saber 608x1080, minecraft 720x1280).
        norm = "[0:v]scale=1080:1920:force_original_aspect_ratio=decrease," \
               "pad=1080:1920:(ow-iw)/2:(oh-ih)/2,setsar=1[bg];"
        if grade_filter:
            filter_str = (
                f"{norm}[bg][1:v]overlay={x_pos}:{y_pos}[stacked];"
                f"[stacked]{grade_filter}[graded];"
                f"[graded]subtitles='{escaped_ass_path}'[v]"
            )
        else:
            filter_str = (
                f"{norm}[bg][1:v]overlay={x_pos}:{y_pos}[stacked];"
                f"[stacked]subtitles='{escaped_ass_path}'[v]"
            )

        # Media inputs must be ABSOLUTE: with cwd=tempdir (so the relative ASS resolves),
        # relative gameplay/sprite/audio paths would break. Abs paths work regardless of cwd.
        _gp_abs = os.path.abspath(gameplay_video_path)
        _sp_abs = os.path.abspath(tmp_sprite)
        _sp_rel = os.path.basename(tmp_sprite)  # relative -> resolves via cwd=tempdir (libass-safe)
        _au_abs = os.path.abspath(audio_path)
        _out_abs = os.path.abspath(output_mp4_path)
        sub_cmd = ["ffmpeg", "-y",
                   "-stream_loop", "-1", "-i", _gp_abs,
                   "-i", _sp_rel,
                   "-i", _au_abs,
                   "-filter_complex", filter_str,
                   "-map", "[v]", "-map", "2:a",
                   *vcodec_args(),
                   "-c:a", "aac", "-b:a", "128k", "-shortest", _out_abs]
        nosub_cmd = ["ffmpeg", "-y",
                     "-stream_loop", "-1", "-i", _gp_abs,
                     "-i", _sp_rel,
                     "-i", _au_abs,
                     "-filter_complex",
                     f"[0:v][1:v]overlay={x_pos}:{y_pos}[v]",
                     "-map", "[v]", "-map", "2:a",
                     *vcodec_args(),
                     "-c:a", "aac", "-b:a", "128k", "-shortest", _out_abs]

        def _run(cmd, cwd=None):
            loop = _asyncio.get_event_loop()
            return loop.run_in_executor(None, lambda: subprocess.run(cmd, capture_output=True, text=True, cwd=cwd))

        proc = await _run(sub_cmd, cwd=_tempfile.gettempdir())
        if proc.returncode != 0 or not os.path.exists(_out_abs) or os.path.getsize(_out_abs) < 1024:
            logger.warning(f"Sprite-reel subtitle-burn failed (code {proc.returncode}); retry without subtitles.")
            with open(os.path.join(_tempfile.gettempdir(), "_burn_err.txt"), "w") as _ef:
                _ef.write(f"rc={proc.returncode}\nCMD:\n{cmd_str}\n\nSTDERR:\n{proc.stderr}\n")
            proc = await _run(nosub_cmd, cwd=_tempfile.gettempdir())
            if proc.returncode != 0:
                logger.error(f"Sprite-reel render failed: {proc.stderr}")
                raise RuntimeError(f"Sprite-reel render failed: {proc.stderr}")
        try:
            os.remove(ass_path)
        except OSError:
            pass
        try:
            os.remove(tmp_sprite)
        except OSError:
            pass
        logger.info(f"Sprite-reel saved to: {output_mp4_path}")
        return output_mp4_path

    @staticmethod
    async def compose_sprite_debate(left_sprite: str, right_sprite: str, gameplay_video_path: str,
                                    audio_path: str, words: List[Dict[str, Any]],
                                    output_mp4_path: str, sprite_scale: float = 0.45) -> str:
        """
        Two-character DEBATE format: left sprite + right sprite over full-frame gameplay,
        sequential narration (left speaks, then right), one grouped-subtitle track.
        Used for Goku-vs-Vegeta style 'character debate' reels.
        The audio is already concatenated sequentially by the caller (no overlap -> no garble).
        """
        import asyncio as _asyncio
        from PIL import Image
        ass_path = output_mp4_path.replace(".mp4", ".ass")
        VideoGenerator.generate_ass_subtitles(words, ass_path)
        escaped_ass_path = ass_path.replace("\\", "/").replace(":", "\\:")

        def prep(sprite_path):
            sp = Image.open(sprite_path).convert("RGBA")
            transparent = Image.new("RGBA", sp.size, (0, 0, 0, 0))
            sp = Image.alpha_composite(transparent, sp)
            target_h = int(1920 * sprite_scale)
            max_h = int(1920 * 0.7)
            if target_h > max_h:
                target_h = max_h
            ratio = target_h / sp.height
            sp = sp.resize((int(sp.width * ratio), target_h), Image.Resampling.LANCZOS)
            tmp = str(Path(output_mp4_path).with_suffix("")) + f"_sp_{abs(hash(sprite_path))}.png"
            sp.save(tmp)
            return tmp

        tmp_l = prep(left_sprite)
        tmp_r = prep(right_sprite)

        filter_str = (
            f"[0:v][1:v]overlay=40:H-overlay_h-80[l];"
            f"[l][2:v]overlay=W-overlay_w-40:H-overlay_h-80[stacked];"
            f"[stacked]subtitles='{escaped_ass_path}':force_style='Alignment=2,MarginV=120'[v]"
        )
        cmd = ["ffmpeg", "-y",
               "-stream_loop", "-1", "-i", gameplay_video_path,
               "-i", tmp_l,
               "-i", tmp_r,
               "-i", audio_path,
               "-filter_complex", filter_str,
               "-map", "[v]", "-map", "3:a",
               *vcodec_args(),
               "-c:a", "aac", "-b:a", "128k", "-shortest", output_mp4_path]

        def _run(c):
            loop = _asyncio.get_event_loop()
            return loop.run_in_executor(None, lambda: subprocess.run(c, capture_output=True, text=True))

        proc = await _run(cmd)
        for t in (tmp_l, tmp_r, ass_path):
            try:
                os.remove(t)
            except OSError:
                pass
        if proc.returncode != 0 or os.path.getsize(output_mp4_path) < 1024:
            logger.error(f"Debate render failed: {proc.stderr}")
            raise RuntimeError(f"Debate render failed: {proc.stderr}")
        logger.info(f"Debate-reel saved to: {output_mp4_path}")
        return output_mp4_path

    @staticmethod
    async def compose_faceless_reel(show_asset_path: str, gameplay_video_path: str, audio_path: str, words: List[Dict[str, Any]], output_mp4_path: str) -> str:
        """
        Stitches a gameplay clip (bottom half) with a show clip or image (top half) in a highly
        engaging split-screen format. Overlays word-by-word animated subtitles.
        """
        
        # Escape path backslashes for FFmpeg subtitle filter
        escaped_ass_path = ass_path.replace("\\", "/").replace(":", "\\:")
        
        logger.info(f"Composing final split-screen reel: Show={show_asset_path}, Gameplay={gameplay_video_path}")
        
        is_image = show_asset_path.lower().endswith(('.png', '.jpg', '.jpeg'))
        
        cmd = ["ffmpeg", "-y"]
        
        if is_image:
            # Loop image input
            cmd.extend(["-loop", "1", "-i", show_asset_path])
        else:
            # Loop video B-roll clip input
            cmd.extend(["-stream_loop", "-1", "-i", show_asset_path])
            
        # Loop gameplay video B-roll clip input
        cmd.extend(["-stream_loop", "-1", "-i", gameplay_video_path])
        
        # Audio input (TTS narration)
        cmd.extend(["-i", audio_path])
        
        # Filter:
        # [0:v] is show clip/image, scaled to 1080x960
        # [1:v] is gameplay, scaled to 1080x960
        # They are stacked vertically, then subtitles are burned on top
        filter_str = (
            "[0:v]scale=w=1080:h=960:force_original_aspect_ratio=increase,crop=1080:960[show];"
            "[1:v]crop=w='min(in_w,in_h*9/16)':h='min(in_h,in_w*16/9)',scale=1080:960[gameplay];"
            "[show][gameplay]vstack=inputs=2[stacked];"
            f"[stacked]subtitles='{escaped_ass_path}':force_style='Alignment=5,MarginV=960'[v]"
        )
        
        sub_cmd = ["ffmpeg", "-y"]
        if is_image:
            sub_cmd.extend(["-loop", "1", "-i", show_asset_path])
        else:
            sub_cmd.extend(["-stream_loop", "-1", "-i", show_asset_path])
        sub_cmd.extend(["-stream_loop", "-1", "-i", gameplay_video_path])
        sub_cmd.extend(["-i", audio_path])
        sub_cmd.extend([
            "-filter_complex", filter_str,
            "-map", "[v]", "-map", "2:a",
            *vcodec_args(),
            "-c:a", "aac", "-b:a", "128k",
            "-shortest",
            output_mp4_path
        ])
        # No-subtitle fallback (captions are nice-to-have, video must not be corrupt)
        nosub_cmd = ["ffmpeg", "-y"]
        if is_image:
            nosub_cmd.extend(["-loop", "1", "-i", show_asset_path])
        else:
            nosub_cmd.extend(["-stream_loop", "-1", "-i", show_asset_path])
        nosub_cmd.extend(["-stream_loop", "-1", "-i", gameplay_video_path])
        nosub_cmd.extend(["-i", audio_path])
        nosub_cmd.extend([
            "-filter_complex",
            "[0:v]scale=w=1080:h=960:force_original_aspect_ratio=increase,crop=1080:960[show];"
            "[1:v]crop=w='min(in_w,in_h*9/16)':h='min(in_h,in_w*16/9)',scale=1080:960[gameplay];"
            "[show][gameplay]vstack=inputs=2[stacked];"
            "[stacked]null[v]",
            "-map", "[stacked]", "-map", "2:a",
            *vcodec_args(),
            "-c:a", "aac", "-b:a", "128k",
            "-shortest",
            output_mp4_path
        ])

        def _run(cmd):
            loop = asyncio.get_event_loop()
            return loop.run_in_executor(None, lambda: subprocess.run(cmd, capture_output=True, text=True))

        logger.info(f"Executing FFmpeg Faceless Composite (with subtitles): {' '.join(sub_cmd)}")
        proc = await _run(sub_cmd)
        if proc.returncode != 0 or not os.path.exists(output_mp4_path) or os.path.getsize(output_mp4_path) < 1024:
            logger.warning(f"FFmpeg faceless subtitle-burn failed (code {proc.returncode}); retrying without subtitles.")
            proc = await _run(nosub_cmd)
            if proc.returncode != 0:
                logger.error(f"FFmpeg failed (code {proc.returncode}): {proc.stderr}")
                raise RuntimeError(f"FFmpeg split-screen render failed: {proc.stderr}")

        logger.info(f"Faceless split-screen reel saved to: {output_mp4_path}")
        return output_mp4_path

