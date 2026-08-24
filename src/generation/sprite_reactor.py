import os
import logging
import random
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

from src.core.config import config
from src.core.config import BASE_DIR
from src.llm.router import router, TaskType
from src.generation import tts as TTS
from src.generation import video as VID
from src.generation import voice_provider as VOICE
from src.generation import emotion as EM
logger = logging.getLogger("content_engine.sprite_reactor")


class SpriteReactor:
    """Contextual sprite-react faceless format (topic + character lens).

    A post is built from two independent choices:
      - TOPIC: what the account is about (fitness, discipline, ...) — dedicated per account
      - CHARACTER: which "host" delivers it (Goku/Vegeta/Gohan) — each has a distinct
        voice_persona, sprite, and TTS voice. The character's angle is a FACET of their
        personality applied to the topic, not the whole subject.

    Flow: pick topic -> pick character -> LLM writes a hook in that character's voice on
    that topic -> TTS in that character's voice -> overlay that character's sprite on gameplay.
    """

    SYSTEM_PROMPT = (
        "You write a short, punchy first-person hook (2-4 sentences) for a faceless reaction reel. "
        "RULES:\n"
        "1. Write in the EXACT voice of the character described — their worldview, their ego, their way of talking.\n"
        "2. The subject is the TOPIC given. The character's personality is the LENS, not the subject.\n"
        "3. Stay factually plausible for the character's universe; do not invent specific lore numbers.\n"
        "4. No intro, no hashtags, no 'in this video', no meta-commentary. Sound like the character is actually saying it.\n"
        "5. Memorable and specific, not generic motivation-poster platitudes."
    )

    @staticmethod
    def _get_account(account_id: str) -> Dict[str, Any]:
        chars = config.load_characters()
        for ck, cv in chars.items():
            if cv.get("id") == account_id:
                return cv
        return {}

    @staticmethod
    def _pick_sprite(sprite_folder: str, tags: List[str], character_key: str = None) -> str:
        """Tag-based sprite selection (legacy/fallback used by the debate path).

        Prefers the per-character folder (data/characters/<char>/sprites) when a
        character_key is given, else `sprite_folder`. Picks a PNG whose filename
        contains any of `tags` (first match wins), then falls back to a random
        sprite in the folder, then "".
        """
        folders = []
        if character_key:
            folders.append(BASE_DIR / "data" / "characters" / character_key / "sprites")
        if sprite_folder:
            folders.append(Path(sprite_folder))
        lowered = [t.lower() for t in tags]
        for folder in folders:
            if not folder.exists():
                continue
            pngs = list(folder.glob("*.png"))
            if not pngs:
                continue
            for p in pngs:
                name = p.stem.lower()
                if any(tag in name for tag in lowered):
                    return str(p)
            return str(random.choice(pngs))
        return ""

    @staticmethod
    async def pick_sprite_llm(character_key: str, emotion: str,
                               context: str = "") -> Tuple[str, float, str]:
        """Semantic sprite selection via LLM (replaces string-tag matching).

        Returns (sprite_path, confidence, reason). The LLM matches the emotion/moment
        to the best available sprite by MEANING (e.g. 'angry' -> 'raging'/'fighting'),
        not literal filename equality. Falls back to a random sprite on low confidence
        or error so a reel is never blocked.
        """
        sprite_name, conf, reason = await EM.llm_pick_sprite(character_key, emotion, context)
        if not sprite_name:
            return ("", 0.0, reason)
        sprite_path = str(BASE_DIR / "data" / "characters" / character_key / "sprites" / sprite_name)
        if not os.path.exists(sprite_path):
            # fall back to any sprite in the folder
            folder = BASE_DIR / "data" / "characters" / character_key / "sprites"
            pngs = list(folder.glob("*.png")) if folder.exists() else []
            if pngs:
                sprite_path = str(random.choice(pngs))
                conf = 0.3
                reason = "resolved mismatch -> random in folder"
        return (sprite_path, conf, reason)

    @staticmethod
    async def generate_hook(topic: str, character: Dict[str, Any], account_name: str) -> str:
        persona = character.get("voice_persona", "")
        prompt = (
            f"Account: {account_name}\n"
            f"Topic of this post: {topic}\n"
            f"Character delivering it: {persona}\n"
            f"Write the hook now:"
        )
        try:
            text = await router.generate(prompt, system_prompt=SpriteReactor.SYSTEM_PROMPT,
                                         task=TaskType.SIMPLE, temperature=0.9)
            return text.strip().lstrip("\"'").strip()
        except Exception as e:
            logger.error(f"Sprite hook generation failed: {e}")
            return f"{topic} — {persona}"

    @staticmethod
    async def produce_reel(account_id: str, output_path: str, sprite_scale: float = 0.35,
                           topic: Optional[str] = None, character_key: Optional[str] = None,
                           emotion: str = "neutral") -> Dict[str, Any]:
        char_conf = SpriteReactor._get_account(account_id)
        if not char_conf:
            raise ValueError(f"Account {account_id} not found")

        topics = char_conf.get("topics", [])
        characters = char_conf.get("characters", {})
        if not topics or not characters:
            raise ValueError(f"Account {account_id} missing topics or characters")

        topic = topic or random.choice(topics)
        character_key = character_key or random.choice(list(characters.keys()))
        character = characters[character_key]

        sprite_folder = char_conf.get("sprite_folder", "data/sprites_clean")
        gameplay_folder = char_conf.get("gameplay_folder", "data/gameplay")
        voice = character.get("tts_voice", char_conf.get("voice", "en-US-GuyNeural"))
        account_name = char_conf.get("name", account_id)

        # 1. Hook in the character's voice on the topic
        script = await SpriteReactor.generate_hook(topic, character, account_name)
        logger.info(f"[{account_id}] topic='{topic}' char='{character_key}' -> '{script[:60]}...'")

        # 2. TTS in that character's voice (RVC if model ready, else Edge-TTS narrator)
        audio_path = str(Path(output_path).with_suffix("")) + "_tts.wav"
        vres = await VOICE.generate_voice(script, character_key, audio_path, voice, emotion=emotion)
        logger.info(f"[{account_id}] voice method={vres['method']} emotion={emotion} for {character_key}")

        # 3. Subtitle timestamps
        words = await VID.VideoGenerator.transcribe_audio_deepgram(audio_path)

        # 4. Semantic sprite pick for this character + emotion moment
        sprite_path, conf, reason = await SpriteReactor.pick_sprite_llm(
            character_key, emotion, context=script[:120])
        if not sprite_path:
            raise RuntimeError("No sprite available for character")
        logger.info(f"[{account_id}] sprite={os.path.basename(sprite_path)} conf={conf:.2f} ({reason})")

        # 5. Gameplay clip
        gp_clips = list(Path(gameplay_folder).glob("*.mp4"))
        gameplay = str(random.choice(gp_clips)) if gp_clips else None
        if not gameplay:
            raise RuntimeError("No gameplay clip available")

        # 6. Composite with emotion-driven video grade
        grade = EM.get_video_grade(emotion)
        await VID.VideoGenerator.compose_sprite_reel(
            sprite_path, gameplay, audio_path, words, output_path,
            sprite_scale=sprite_scale, grade=grade)

        return {
            "account_id": account_id,
            "topic": topic,
            "character": character_key,
            "emotion": emotion,
            "sprite": os.path.basename(sprite_path),
            "sprite_confidence": round(conf, 2),
            "voice": voice,
            "script": script,
            "path": output_path,
        }

    @staticmethod
    async def produce_debate(account_id: str, output_path: str, topic: str,
                             left_char: str, right_char: str, sprite_scale: float = 0.45) -> Dict[str, Any]:
        """Produce a two-character DEBATE reel: left_char vs right_char, each with their own
        voice + sprite, both lines over gameplay, grouped subtitles for both."""
        char_conf = SpriteReactor._get_account(account_id)
        if not char_conf:
            raise ValueError(f"Account {account_id} not found")
        characters = char_conf.get("characters", {})
        if left_char not in characters or right_char not in characters:
            raise ValueError(f"Characters {left_char}/{right_char} not in account")
        sprite_folder = char_conf.get("sprite_folder", "data/sprites_clean")
        gameplay_folder = char_conf.get("gameplay_folder", "data/gameplay")
        account_name = char_conf.get("name", account_id)

        async def build_side(char_key):
            ch = characters[char_key]
            persona = ch.get("voice_persona", "")
            prompt = (
                f"Account: {account_name}\nTopic: {topic}\n"
                f"Character: {persona}\n"
                f"You are in a DEBATE. Deliver ONE short rebuttal/opinion (1-2 sentences) in your voice.\n"
                f"Write it now:"
            )
            try:
                txt = await router.generate(prompt, system_prompt=SpriteReactor.SYSTEM_PROMPT,
                                            task=TaskType.SIMPLE, temperature=0.9)
                txt = txt.strip().lstrip("\"'").strip()
            except Exception as e:
                logger.error(f"Debate script failed for {char_key}: {e}")
                txt = f"{topic} — {persona}"
            voice = ch.get("tts_voice", "en-US-GuyNeural")
            audio = str(Path(output_path).with_suffix("")) + f"_{char_key}_tts.wav"
            vres = await VOICE.generate_voice(txt, char_key, audio, voice)
            logger.info(f"[debate] voice method={vres['method']} for {char_key}")
            sprite = SpriteReactor._pick_sprite(sprite_folder, ch.get("sprite_tags", []), char_key)
            return txt, audio, sprite

        l_txt, l_audio, l_sprite = await build_side(left_char)
        r_txt, r_audio, r_sprite = await build_side(right_char)

        gp_clips = list(Path(gameplay_folder).glob("*.mp4"))
        gameplay = str(random.choice(gp_clips)) if gp_clips else None
        if not gameplay or not l_sprite or not r_sprite:
            raise RuntimeError("Missing gameplay or sprites for debate")

        # Sequence the two speakers (left, then a short gap, then right) so audio does not
        # overlap. Concat into one track, then transcribe once -> clean non-overlapping words.
        import asyncio as _asyncio
        import subprocess as _sp
        combined = str(Path(output_path).with_suffix("")) + "_combined.wav"
        # 400ms silence between speakers
        silence = str(Path(output_path).with_suffix("")) + "_silence.wav"
        _sp.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=24000:d=0.4",
                  "-c:a", "pcm_s16le", silence], capture_output=True)
        _sp.run(["ffmpeg", "-y", "-i", l_audio, "-i", silence, "-i", r_audio,
                  "-filter_complex", "[0:a][1:a][2:a]concat=n=3:v=0:a=1[out]",
                  "-map", "[out]", combined], capture_output=True)
        words = await VID.VideoGenerator.transcribe_audio_deepgram(combined)

        await VID.VideoGenerator.compose_sprite_debate(
            l_sprite, r_sprite, gameplay, combined, words, output_path, sprite_scale=sprite_scale)

        for t in (l_audio, r_audio, silence, combined):
            try:
                os.remove(t)
            except OSError:
                pass

        return {
            "account_id": account_id,
            "topic": topic,
            "left": {"char": left_char, "sprite": os.path.basename(l_sprite), "script": l_txt},
            "right": {"char": right_char, "sprite": os.path.basename(r_sprite), "script": r_txt},
            "path": output_path,
        }

    @staticmethod
    async def produce_scripted_reel(account_id: str, output_path: str, character_key: str,
                                    lines: List[Dict[str, str]], sprite_scale: float = 0.35,
                                    topic: str = "") -> Dict[str, Any]:
        """Produce a reel from a PLANNER-EMITTED script: a list of {text, emotion} lines.

        Each line is rendered with its OWN emotion -> voice prosody + per-char RVC pitch +
        LLM-chosen sprite + video grade. Segments are concatenated so the reel MODULATES
        tonality and PNGs across the clip (the flexibility you asked for).
        """
        char_conf = SpriteReactor._get_account(account_id)
        if not char_conf:
            raise ValueError(f"Account {account_id} not found")
        characters = char_conf.get("characters", {})
        if character_key not in characters:
            raise ValueError(f"Character {character_key} not in account")
        character = characters[character_key]
        gameplay_folder = char_conf.get("gameplay_folder", "data/gameplay")
        voice = character.get("tts_voice", char_conf.get("voice", "en-US-GuyNeural"))

        gp_clips = list(Path(gameplay_folder).glob("*.mp4"))
        if not gp_clips:
            raise RuntimeError("No gameplay clip available")
        gameplay = str(random.choice(gp_clips))

        seg_audio, seg_meta = [], []
        base = str(Path(output_path).with_suffix(""))
        for i, ln in enumerate(lines):
            text = ln.get("text", "").strip()
            emotion = ln.get("emotion", "neutral")
            if not text:
                continue
            a_path = f"{base}_seg{i}.wav"
            vres = await VOICE.generate_voice(text, character_key, a_path, voice, emotion=emotion)
            sprite_path, conf, reason = await SpriteReactor.pick_sprite_llm(
                character_key, emotion, context=text[:120])
            grade = EM.get_video_grade(emotion)
            seg_audio.append(a_path)
            seg_meta.append({
                "text": text, "emotion": emotion,
                "sprite": os.path.basename(sprite_path) if sprite_path else "",
                "sprite_confidence": round(conf, 2), "grade": grade,
            })
            logger.info(f"[scripted] seg{i} emo={emotion} sprite={os.path.basename(sprite_path)} conf={conf:.2f} grade={grade}")

        if not seg_audio:
            raise RuntimeError("No lines rendered")

        # Concatenate audio segments back-to-back with short gaps.
        import subprocess as _sp
        silence = f"{base}_silence.wav"
        _sp.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=24000:d=0.25",
                 "-c:a", "pcm_s16le", silence], capture_output=True)
        inputs = []
        for a in seg_audio:
            inputs += ["-i", a]
        inputs += ["-i", silence] * (len(seg_audio) - 1)
        # Build concat filter for alternating seg/silence.
        fchain = ""
        ainputs = len(seg_audio)
        # interleave: seg0, silence, seg1, silence, ...
        order = []
        for i in range(ainputs):
            order.append(f"[{i}:a]")
            if i < ainputs - 1:
                order.append(f"[{ainputs + i}:a]")
        concat_filter = "".join(order) + f"concat=n={len(order)}:v=0:a=1[out]"
        combined = f"{base}_combined.wav"
        _sp.run(["ffmpeg", "-y", *inputs, "-filter_complex", concat_filter,
                 "-map", "[out]", combined], capture_output=True)

        # Composite each segment over gameplay with its own sprite + grade, then concat video.
        seg_videos = []
        for i, meta in enumerate(seg_meta):
            sprite_path = str(BASE_DIR / "data" / "characters" / character_key / "sprites" / meta["sprite"]) \
                if meta["sprite"] else ""
            if not sprite_path or not os.path.exists(sprite_path):
                folder = BASE_DIR / "data" / "characters" / character_key / "sprites"
                pngs = list(folder.glob("*.png")) if folder.exists() else []
                sprite_path = str(random.choice(pngs)) if pngs else ""
            v_seg = f"{base}_vseg{i}.mp4"
            # Transcribe per-seg words; if Deepgram fails, render without subtitles.
            try:
                seg_words = await VID.VideoGenerator.transcribe_audio_deepgram(seg_audio[i])
            except Exception as e:
                logger.warning(f"[scripted] seg{i} transcribe failed ({e}); no subtitles")
                seg_words = []
            try:
                await VID.VideoGenerator.compose_sprite_reel(
                    sprite_path, gameplay, seg_audio[i], seg_words, v_seg,
                    sprite_scale=sprite_scale, grade=meta["grade"])
            except Exception as e:
                logger.error(f"[scripted] seg{i} composite failed: {e}")
                continue
            if os.path.exists(v_seg):
                seg_videos.append(v_seg)

        if not seg_videos:
            raise RuntimeError("No video segments rendered")

        # Concatenate segment videos via filter_complex. Normalize each input to a
        # fixed 1080x1920 / SAR=1 first so the concat filter never chokes on
        # differing resolutions (gameplay clips vary 720x1280 vs 1080x1920).
        # ffmpeg concat expects inputs INTERLEAVED per segment: [v0][a0][v1][a1]...
        n = len(seg_videos)
        inputs = []
        for v in seg_videos:
            inputs += ["-i", v]
        vchain = ""
        for i in range(n):
            vchain += (f"[{i}:v]scale=1080:1920:force_original_aspect_ratio=decrease,"
                       f"pad=1080:1920:(ow-iw)/2:(oh-ih)/2,setsar=1[v{i}];")
        # interleave: seg0 video, seg0 audio, seg1 video, seg1 audio, ...
        interleave = "".join(f"[v{i}][{i}:a]" for i in range(n))
        filter_c = f"{vchain}{interleave}concat=n={n}:v=1:a=1[v][a]"
        r = _sp.run(["ffmpeg", "-y", *inputs, "-filter_complex", filter_c,
                     "-map", "[v]", "-map", "[a]",
                     "-c:v", "libx264", "-preset", "ultrafast", "-crf", "26",
                     "-c:a", "aac", "-b:a", "128k", output_path],
                    capture_output=True, text=True)
        if r.returncode != 0 or not os.path.exists(output_path):
            logger.error(f"[scripted] final concat failed: {r.stderr[-400:]}")
            raise RuntimeError(f"Scripted reel concat failed (rc={r.returncode})")

        for t in seg_audio + [silence, combined] + seg_videos:
            try:
                os.remove(t)
            except OSError:
                pass

        return {
            "account_id": account_id,
            "character": character_key,
            "topic": topic,
            "lines": seg_meta,
            "path": output_path,
        }

    @staticmethod
    @staticmethod
    async def produce_account_debate(account_id: str, output_path: str, topic: str = None,
                                     sprite_scale: float = 0.35,
                                     num_turns: int = None) -> Dict[str, Any]:
        """GENERIC faceless debate reel driven entirely by the account's YAML config.

        Reads from characters.yaml: speakers (leads), cameos, cameo_prob, tone,
        trading_angle, seg_range, prompt_templates. Prompts are built by PromptBuilder
        from the account's template strings ({{variables}} plugged at runtime). Topic
        is auto-picked from the account's topic_universe unless passed in (trend scout
        can supply it later). Lean FacelessMemory supplies {{recent_topics}} for dedup.

        This replaces the old hardcoded produce_tate_vs_peppa — same behaviour, but
        the per-account variation lives in YAML, not code.
        """
        from src.generation.prompt_builder import PromptBuilder, FacelessMemory

        char_conf = SpriteReactor._get_account(account_id)
        if not char_conf:
            raise ValueError(f"Account {account_id} not found in characters.yaml")
        if char_conf.get("type") != "faceless" and char_conf.get("pipeline") != "debate_2lead_cameo":
            logger.warning(f"[debate] account {account_id} type={char_conf.get('type')}; proceeding")

        roster = {}
        for ck, cd in (char_conf.get("characters") or {}).items():
            roster[ck] = {
                "persona": cd.get("voice_persona", ""),
                "sprite_tags": cd.get("sprite_tags", [ck]),
                "tts_voice": cd.get("tts_voice", "en-US-GuyNeural"),
            }

        leads = list(char_conf.get("speakers", [])) or list(roster.keys())[:2]
        for l in leads:
            if l not in roster:
                raise ValueError(f"Lead '{l}' not in account characters")
        cameos = [c for c in (char_conf.get("cameos") or []) if c in roster]
        cameo_prob = float(char_conf.get("cameo_prob", 0.0))
        tone = char_conf.get("tone", "funny debate")
        trading_angle = bool(char_conf.get("trading_angle", False))
        seg_range = char_conf.get("seg_range", [8, 16])
        account_name = char_conf.get("name", account_id)
        role = char_conf.get("role", "")

        if not topic:
            universe = char_conf.get("topic_universe") or []
            topic = random.choice(universe) if universe else "a surprising global event"

        if num_turns is None:
            lo, hi = (seg_range + [16])[:2]
            num_turns = random.randint(int(lo), int(hi))

        builder = PromptBuilder(char_conf)
        mem = FacelessMemory(account_id)

        SYSTEM = SpriteReactor.SYSTEM_PROMPT
        turns: List[Dict[str, str]] = []
        ctx_lines: List[str] = []
        recent = mem.recent_topics(limit=8)
        recent_str = "; ".join(recent) if recent else "(none yet)"

        for t in range(num_turns):
            if cameos and t > 2 and random.random() < cameo_prob:
                sp = random.choice(cameos)
                is_cameo = True
            else:
                sp = leads[t % len(leads)]
                is_cameo = False
            persona = roster[sp]["persona"]
            ctx = "\n".join(f"- {c}" for c in ctx_lines[-5:]) if ctx_lines else "(start of debate)"
            trading_note = ("Add a sharp, plausible trading angle (commodity/fx/equity/rates) — keep it funny. "
                            if trading_angle else "")
            context = {
                "topic": topic, "speaker": sp, "persona": persona, "tone": tone,
                "account_name": account_name, "role": role, "ctx": ctx,
                "recent_topics": recent_str, "trading_note": trading_note,
            }
            if is_cameo:
                prompt = (f"TOPIC: {topic}\nCAMEO GUEST: {sp} — {persona}\nWHAT'S BEEN SAID:\n{ctx}\n"
                          f"You are a CAMEO guest dropping a short, funny, out-of-context one-liner as {sp} "
                          f"in their exact voice (snack complaint, random observation, petty interruption). "
                          f"Output ONLY the line, no quotes.")
            else:
                prompt = builder.build(context)
            text = None
            for attempt in range(3):
                try:
                    text = (await router.generate(prompt, system_prompt=SYSTEM,
                                                  task=TaskType.SIMPLE, temperature=0.95)).strip()
                    if text:
                        break
                except Exception as ge:
                    logger.warning(f"[debate] turn {t} ({sp}) gen attempt {attempt} failed: {ge}")
            text = (text or "").strip().lstrip("\"'").strip()
            if not text:
                text = f"{sp} has no comment."
            emotion = random.choice(["neutral", "excited", "angry", "sarcastic", "smug", "determined"])
            turns.append({"speaker": sp, "text": text, "emotion": emotion, "cameo": is_cameo})
            ctx_lines.append(f"{sp}: {text}")

        mem.record_topic(topic)

        # ---- render ----
        # Use VideoGenerator.get_gameplay_clip() which auto-falls-back to downloading
        # Pexels vertical stock B-roll when no local gameplay clips exist. (Do NOT
        # raise here — the fallback keeps the pipeline self-sufficient.)
        try:
            gameplay = await VID.VideoGenerator.get_gameplay_clip()
        except Exception as ge:
            gp_clips = list((BASE_DIR / "data" / "gameplay").glob("*.mp4"))
            if not gp_clips:
                raise RuntimeError(f"No gameplay clip available and Pexels fallback failed: {ge}")
            gameplay = str(random.choice(gp_clips))
        base = str(Path(output_path).with_suffix(""))
        seg_audio, seg_videos, seg_meta = [], [], []
        for i, turn in enumerate(turns):
            sp = turn["speaker"]; text = turn["text"]; emotion = turn["emotion"]
            if not text:
                continue
            a_path = f"{base}_seg{i}.wav"
            vres = await VOICE.generate_voice(text, sp, a_path, roster[sp]["tts_voice"], emotion=emotion)
            sprite_path, conf, _ = await SpriteReactor.pick_sprite_llm(sp, emotion, context=text[:120])
            grade = EM.get_video_grade(emotion)
            v_seg = f"{base}_vseg{i}.mp4"
            try:
                seg_words = await VID.VideoGenerator.transcribe_audio_deepgram(a_path)
            except Exception as e:
                logger.warning(f"[tvp] turn {i} transcribe failed ({e})"); seg_words = []
            try:
                await VID.VideoGenerator.compose_sprite_reel(sprite_path, gameplay, a_path, seg_words, v_seg,
                                                            sprite_scale=sprite_scale, grade=grade)
            except Exception as e:
                logger.error(f"[tvp] turn {i} composite failed: {e}"); continue
            if os.path.exists(v_seg):
                seg_audio.append(a_path); seg_videos.append(v_seg)
                seg_meta.append({"speaker": sp, "text": text, "emotion": emotion,
                                 "sprite": os.path.basename(sprite_path) if sprite_path else "",
                                 "method": vres["method"], "cameo": turn.get("cameo", False)})
        if not seg_videos:
            raise RuntimeError("No tvp segments rendered")
        # audio concat
        import subprocess as _sp
        silence = f"{base}_silence.wav"
        _sp.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=24000:d=0.35", "-c:a", "pcm_s16le", silence], capture_output=True)
        a_inputs = []
        for a in seg_audio:
            a_inputs += ["-i", a]
        a_inputs += ["-i", silence] * (len(seg_audio) - 1)
        aorder = []
        for i in range(len(seg_audio)):
            aorder.append(f"[{i}:a]")
            if i < len(seg_audio) - 1:
                aorder.append(f"[{len(seg_audio)+i}:a]")
        a_filter = "".join(aorder) + f"concat=n={len(aorder)}:v=0:a=1[out]"
        combined = f"{base}_combined.wav"
        _sp.run(["ffmpeg", "-y", *a_inputs, "-filter_complex", a_filter, "-map", "[out]", combined], capture_output=True)
        n = len(seg_videos)
        v_inputs = []
        for v in seg_videos:
            v_inputs += ["-i", v]
        vchain = ""
        for i in range(n):
            vchain += (f"[{i}:v]scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2,setsar=1[v{i}];")
        interleave = "".join(f"[v{i}][{i}:a]" for i in range(n))
        v_filter = f"{vchain}{interleave}concat=n={n}:v=1:a=1[v][a]"
        r = _sp.run(["ffmpeg", "-y", *v_inputs, "-filter_complex", v_filter, "-map", "[v]", "-map", "[a]",
                     "-c:v", "libx264", "-preset", "ultrafast", "-crf", "26", "-c:a", "aac", "-b:a", "128k", output_path],
                    capture_output=True, text=True)
        if r.returncode != 0 or not os.path.exists(output_path):
            logger.error(f"[tvp] concat failed: {r.stderr[-400:]}")
            raise RuntimeError(f"tvp concat failed (rc={r.returncode})")
        for t in seg_audio + [silence, combined] + seg_videos:
            try: os.remove(t)
            except OSError: pass
        return {"account_id": account_id, "topic": topic, "turns": seg_meta, "path": output_path,
                "duration_note": f"{len(seg_meta)} turns rendered"}

    @staticmethod
    async def produce_tate_vs_peppa(output_path: str, topic: str = None,
                                    cameos: List[str] = None,
                                    num_turns: int = None,
                                    sprite_scale: float = 0.35,
                                    cameo_prob: float = 0.35,
                                    tone: str = None) -> Dict[str, Any]:
        """Backward-compatible wrapper -> generic produce_account_debate('tate_vs_peppa').

        All real behaviour now lives in produce_account_debate, driven by the
        tate_vs_peppa block in characters.yaml. Per-account tuning no longer lives here.
        """
        return await SpriteReactor.produce_account_debate(
            "tate_vs_peppa", output_path, topic=topic, sprite_scale=sprite_scale, num_turns=num_turns)

    @staticmethod
    async def produce_roundtable(output_path: str, topic: str,
                                 speakers: List[str] = None,
                                 num_turns: int = 18,
                                 sprite_scale: float = 0.35,
                                 heckle_ratio: float = 0.0,
                                 tone: str = "funny chaotic panel debate") -> Dict[str, Any]:
        """Produce a multi-character DEBATE / roundtable reel (e.g. ~5 min) where
        every turn is spoken by a different character in their OWN RVC voice + sprite.

        Unlike produce_debate (2 side-by-side speakers), this renders each turn
        single-host (one speaker's sprite + voice at a time) and stitches all
        segments into one vertical reel. Scales to any number of speakers.

        speakers: list of character keys. If None, ALL characters across every
        account are used (new + old). Each gets a roughly equal number of turns.
        heckle_ratio: fraction of turns that are out-of-context heckles/interruptions
        instead of on-topic takes (0.0 = none). tone: style hint for the LLM.
        """
        # Build a merged roster of every character across all accounts.
        all_accounts = config.load_characters()
        roster = {}
        for acc_id, acc in all_accounts.items():
            for ck, cd in (acc.get("characters") or {}).items():
                roster[ck] = {
                    "persona": cd.get("voice_persona", ""),
                    "sprite_tags": cd.get("sprite_tags", [ck]),
                    "tts_voice": cd.get("tts_voice", "en-US-GuyNeural"),
                }
        if speakers:
            roster = {k: v for k, v in roster.items() if k in speakers}
        if not roster:
            raise ValueError("No speakers resolved for roundtable")

        speaker_order = list(roster.keys())
        random.shuffle(speaker_order)

        # ---- 1. Generate the debate script turn-by-turn (continuity via context) ----
        turns: List[Dict[str, str]] = []   # {speaker, text, emotion}
        ctx_lines: List[str] = []
        for t in range(num_turns):
            sp = speaker_order[t % len(speaker_order)]
            persona = roster[sp]["persona"]
            is_heckle = (random.random() < heckle_ratio)
            ctx = "\n".join(f"- {c}" for c in ctx_lines[-4:]) if ctx_lines else "(start)"
            if is_heckle:
                prompt = (
                    f"TOPIC: {topic}\nTHIS SPEAKER: {sp} — {persona}\n"
                    f"WHAT'S BEEN SAID:\n{ctx}\n\n"
                    f"You are heckling / interrupting with a completely OUT-OF-CONTEXT, "
                    f"chaotic one-liner as {sp}, in their exact voice. No setup, just a "
                    f"random funny interruption (complain about snacks, the venue, another "
                    f"speaker, your own problems). Output ONLY the line, no quotes."
                )
            else:
                prompt = (
                    f"TOPIC: {topic}\n"
                    f"THIS SPEAKER: {sp} — {persona}\n"
                    f"WHAT'S BEEN SAID SO FAR:\n{ctx}\n\n"
                    f"You are in a {tone}. Deliver ONE short take "
                    f"(1-2 sentences) as {sp}, in their exact voice and worldview. Be funny "
                    f"and a bit mysterious, stay in character, reference the topic or what "
                    f"someone just said. Output ONLY the line, no quotes, no ' Speaker:' prefix."
                )
            try:
                text = None
                for attempt in range(3):
                    try:
                        text = (await router.generate(prompt, system_prompt=SpriteReactor.SYSTEM_PROMPT,
                                                      task=TaskType.SIMPLE, temperature=0.95)).strip()
                        if text:
                            break
                    except Exception as ge:
                        logger.warning(f"[roundtable] turn {t} ({sp}) gen attempt {attempt} failed: {ge}")
                text = (text or "").strip().lstrip("\"'").strip()
            except Exception as e:
                logger.error(f"[roundtable] turn {t} ({sp}) gen failed: {e}")
                text = f"{sp} has no comment."
            emotion = random.choice(["neutral", "excited", "angry", "sarcastic", "smug", "determined"])
            turns.append({"speaker": sp, "text": text, "emotion": emotion, "heckle": is_heckle})
            ctx_lines.append(f"{sp}: {text}")

        # ---- 2. Render each turn: per-speaker RVC voice + sprite segment ----
        gp_clips = list((BASE_DIR / "data" / "gameplay").glob("*.mp4"))
        if not gp_clips:
            raise RuntimeError("No gameplay clip available")
        gameplay = str(random.choice(gp_clips))

        base = str(Path(output_path).with_suffix(""))
        seg_audio, seg_videos, seg_meta = [], [], []
        for i, turn in enumerate(turns):
            sp = turn["speaker"]
            text = turn["text"]
            emotion = turn["emotion"]
            if not text:
                continue
            a_path = f"{base}_seg{i}.wav"
            vres = await VOICE.generate_voice(text, sp, a_path,
                                              roster[sp]["tts_voice"], emotion=emotion)
            logger.info(f"[roundtable] turn {i} speaker={sp} method={vres['method']}")
            sprite_path, conf, _ = await SpriteReactor.pick_sprite_llm(
                sp, emotion, context=text[:120])
            grade = EM.get_video_grade(emotion)
            v_seg = f"{base}_vseg{i}.mp4"
            try:
                seg_words = await VID.VideoGenerator.transcribe_audio_deepgram(a_path)
            except Exception as e:
                logger.warning(f"[roundtable] turn {i} transcribe failed ({e})")
                seg_words = []
            try:
                await VID.VideoGenerator.compose_sprite_reel(
                    sprite_path, gameplay, a_path, seg_words, v_seg,
                    sprite_scale=sprite_scale, grade=grade)
            except Exception as e:
                logger.error(f"[roundtable] turn {i} composite failed: {e}")
                continue
            if os.path.exists(v_seg):
                seg_audio.append(a_path)
                seg_videos.append(v_seg)
                seg_meta.append({"speaker": sp, "text": text, "emotion": emotion,
                                 "sprite": os.path.basename(sprite_path) if sprite_path else "",
                                 "method": vres["method"]})

        if not seg_videos:
            raise RuntimeError("No roundtable segments rendered")

        # ---- 3. Stitch audio (with gaps) + video segments ----
        import subprocess as _sp
        silence = f"{base}_silence.wav"
        _sp.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=24000:d=0.35",
                 "-c:a", "pcm_s16le", silence], capture_output=True)
        # audio concat: seg0 silence seg1 silence ...
        a_inputs = []
        for a in seg_audio:
            a_inputs += ["-i", a]
        a_inputs += ["-i", silence] * (len(seg_audio) - 1)
        aorder = []
        for i in range(len(seg_audio)):
            aorder.append(f"[{i}:a]")
            if i < len(seg_audio) - 1:
                aorder.append(f"[{len(seg_audio) + i}:a]")
        a_filter = "".join(aorder) + f"concat=n={len(aorder)}:v=0:a=1[out]"
        combined = f"{base}_combined.wav"
        _sp.run(["ffmpeg", "-y", *a_inputs, "-filter_complex", a_filter,
                 "-map", "[out]", combined], capture_output=True)

        n = len(seg_videos)
        v_inputs = []
        for v in seg_videos:
            v_inputs += ["-i", v]
        vchain = ""
        for i in range(n):
            vchain += (f"[{i}:v]scale=1080:1920:force_original_aspect_ratio=decrease,"
                       f"pad=1080:1920:(ow-iw)/2:(oh-ih)/2,setsar=1[v{i}];")
        interleave = "".join(f"[v{i}][{i}:a]" for i in range(n))
        v_filter = f"{vchain}{interleave}concat=n={n}:v=1:a=1[v][a]"
        r = _sp.run(["ffmpeg", "-y", *v_inputs, "-filter_complex", v_filter,
                     "-map", "[v]", "-map", "[a]",
                     "-c:v", "libx264", "-preset", "ultrafast", "-crf", "26",
                     "-c:a", "aac", "-b:a", "128k", output_path],
                    capture_output=True, text=True)
        if r.returncode != 0 or not os.path.exists(output_path):
            logger.error(f"[roundtable] concat failed: {r.stderr[-400:]}")
            raise RuntimeError(f"Roundtable concat failed (rc={r.returncode})")

        for t in seg_audio + [silence, combined] + seg_videos:
            try:
                os.remove(t)
            except OSError:
                pass

        return {
            "topic": topic,
            "speakers": speaker_order,
            "turns": seg_meta,
            "path": output_path,
            "duration_note": f"{len(seg_meta)} turns rendered",
        }

