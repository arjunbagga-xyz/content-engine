"""VoiceProvider — character-accurate TTS with graceful fallback.

Pipeline:
  text -> Edge-TTS (generic narrator, fast, free) -> [if RVC model exists] -> RVC converts
  the narration's TIMBRE to the character's voice -> final character wav.

If no RVC model is trained for a character yet, it falls back to the configured
Edge-TTS voice (so the pipeline never breaks while you source/train voices).

RVC models live in: data/voice_models/<character>/<character>.pth
(plus the companion index file <character>.index if trained with feature index).
"""
import os
import logging
import asyncio
import subprocess
from pathlib import Path

import edge_tts

from src.core.config import config

logger = logging.getLogger("content_engine.voice_provider")

VOICE_MODEL_DIR = Path("data/voice_models")
# Per-character media tree: data/characters/<char>/{sprites,models,loras,media}
CHAR_MODEL_DIR = Path("data/characters")
# Default RVC inference settings (tweak per character if needed)
RVC_DEVICE = "cpu"          # inference runs on CPU; training is on Colab GPU
RVC_INDEX_RATE = 0.66       # v2 models: higher = more speaker texture (less flat/robotic), ~0.66 sweet spot
RVC_PITCH = 0               # 0 = auto; +n = n semitones up, -n down (match char register)
RVC_F0_METHOD = "rmvpe"     # stable pitch for animated voices
RVC_REVERSE = False
RVC_FILTER_RADIUS = 3
RVC_RMS_MIX_RATE = 1.0
RVC_PROTECT = 0.4           # protects unvoiced consonants from buzz

# Map of character -> base Edge-TTS narrator used to author the line before RVC conversion.
# (Same values as characters.yaml tts_voice; kept here so VoiceProvider is self-contained.)
FALLBACK_NARRATOR = {
    "goku": "en-US-GuyNeural",
    "vegeta": "en-US-SteffanNeural",
    "gohan": "en-US-AndrewNeural",
    "peter": "en-US-ChristopherNeural",
    "stewie": "en-US-BrianNeural",
    "meg": "en-US-AriaNeural",
}


def _model_paths(character: str):
    # Prefer the per-character media tree; fall back to legacy flat dir.
    base = CHAR_MODEL_DIR / character / "models"
    if not base.exists():
        base = VOICE_MODEL_DIR / character
    pth = base / f"{character}.pth"
    index = base / f"{character}.index"
    return pth, index if index.exists() else None


def rvc_model_ready(character: str) -> bool:
    pth, _ = _model_paths(character)
    return pth.exists()


def _ffmpeg_decode(src: str, dst: str) -> bool:
    """Decode an audio file to 16-bit PCM WAV @40k via ffmpeg. Returns True on success."""
    import subprocess
    probe = subprocess.run(
        ["ffmpeg", "-y", "-i", src, "-ar", "40000", "-ac", "1", "-sample_fmt", "s16", dst],
        capture_output=True, text=True,
    )
    if probe.returncode == 0 and os.path.exists(dst) and os.path.getsize(dst) > 1024:
        return True
    logger.warning(f"ffmpeg decode failed (rc={probe.returncode}): {probe.stderr[-200:]}")
    return False


async def _edge_tts(text: str, voice: str, out_path: str, prosody: dict = None) -> str:
    """Synthesize with Edge-TTS and write a proper 16-bit PCM WAV.

    `prosody`: optional dict with rate/volume/pitch (from emotion module) applied to the
    narrator. Edge-TTS's Communicate.save() always emits MP3 bytes even when the path
    ends in .wav, so we decode to a real WAV (16-bit PCM, 40 kHz) via ffmpeg.
    """
    import tempfile
    import subprocess

    # Use a real temp dir so the intermediate mp3 never gets a doubled ".wav.mp3"
    # extension on the output path (cosmetic, but keeps logs clean).
    with tempfile.TemporaryDirectory() as td:
        tmp_mp3 = os.path.join(td, "narrator.mp3")

        async def _save_with_retry(build_comm, dest: str, label: str) -> bool:
            """Edge-TTS talks to Microsoft's unofficial endpoint which gets throttled
            and can hang indefinitely. Bound every save with a generous timeout (so a
            slow-but-working response of 1-3 min can still complete), rebuild a FRESH
            Communicate object on each attempt (the stream can only be consumed once),
            and sleep briefly between attempts so a transient DNS drop recovers. The key
            fix vs. the original code is that this is BOUNDED (never infinite)."""
            last_err = None
            per_attempt_timeout = 180  # allow a legitimately slow 2-3 min response to finish
            for attempt in range(3):
                if attempt:
                    await asyncio.sleep(3.0 * attempt)  # backoff 3s, 6s
                comm = build_comm()
                try:
                    await asyncio.wait_for(comm.save(dest), timeout=per_attempt_timeout)
                    if os.path.exists(dest) and os.path.getsize(dest) > 0:
                        return True
                    last_err = "save produced no file"
                except asyncio.TimeoutError:
                    last_err = f"Edge-TTS save timed out after {per_attempt_timeout}s (attempt {attempt+1}/3)"
                    logger.warning(f"{last_err}: voice='{voice}' label={label}")
                except Exception as e:
                    last_err = str(e)[:160]
                    logger.warning(f"Edge-TTS save failed (attempt {attempt+1}/3): {last_err}")
            raise RuntimeError(f"Edge-TTS save failed after 3 attempts: {last_err}")

        # Build the prosody / plain communicators lazily so each retry gets a fresh stream.
        def _comm_prosody():
            return edge_tts.Communicate(text, voice, **prosody) if prosody else edge_tts.Communicate(text, voice)
        def _comm_plain():
            return edge_tts.Communicate(text, voice)

        try:
            await _save_with_retry(_comm_prosody, tmp_mp3, "prosody")
        finally:
            pass
        # Decode MP3 -> clean 16-bit PCM WAV at 40k (matches RVC model sample rate).
        # edge_tts sometimes emits an MP3 variant that ffmpeg's decoder chokes on
        # (esp. with pitch/volume kwargs). Fall back to a plain decode, and if that
        # also fails, retry WITHOUT prosody (the base MP3 always decodes) so voice
        # generation never hard-blocks the reel.
        ok = _ffmpeg_decode(tmp_mp3, out_path)
        if not ok and prosody:
            logger.warning(f"edge_tts MP3 decode failed with prosody; retrying plain narrator")
            plain_mp3 = os.path.join(td, "narrator_plain.mp3")
            await _save_with_retry(_comm_plain, plain_mp3, "plain")
            ok = _ffmpeg_decode(plain_mp3, out_path)
        if not ok:
            raise RuntimeError(f"Failed to decode Edge-TTS MP3 to WAV for voice '{voice}'")
    return out_path


def _rvc_convert(narrator_wav: str, character: str, out_path: str,
                 pitch: int = None) -> str:
    """Convert a narrator wav to the character's voice using the trained RVC model.

    pitch: RVC semitone shift (None -> per-character default from emotion module).
    """
    from src.generation import emotion as EM
    if pitch is None:
        pitch = EM.get_rvc_pitch(character)
    pth, index = _model_paths(character)
    if not pth.exists():
        raise RuntimeError(f"No RVC model for '{character}' at {pth}")

    # This fork (liujing04) ships inference at rvc_repo/infer/cli.py (not tools/infer_cli.py).
    rvc_root = os.environ.get("RVC_ROOT", "")
    rvc_repo = str(Path(__file__).resolve().parent.parent.parent / "rvc_repo")
    rvc_py = str(Path(__file__).resolve().parent.parent.parent / "rvc_env" / "Scripts" / "python.exe")
    if rvc_root:
        infer_script = os.path.join(rvc_root, "infer", "cli.py")
    else:
        # resolve relative to repo root
        infer_script = str(
            (Path(__file__).resolve().parent.parent.parent / "rvc_repo" / "infer" / "cli.py")
        )
    if not os.path.exists(infer_script):
        raise RuntimeError(f"RVC inference CLI not found at {infer_script}")

    cmd = [
        rvc_py, infer_script,
        "--model", str(pth.resolve()),
        "--input", str(Path(narrator_wav).resolve()),
        "--output", str(Path(out_path).resolve()),
        "--pitch", str(pitch),
        "--f0-method", RVC_F0_METHOD,
        "--index-rate", str(RVC_INDEX_RATE if index else 0),
        "--rms-mix-rate", str(RVC_RMS_MIX_RATE),
        "--protect", str(RVC_PROTECT),
        "--overwrite",
    ]
    if index:
        cmd += ["--index", str(Path(index).resolve())]
    # Force the inference device (CPU is fine & leaves GPU free)
    env = dict(os.environ)
    env["RVC_DEVICE"] = RVC_DEVICE

    logger.info(f"RVC convert: {character}  ({'with index' if index else 'no index'})")
    # Critical: strip any inherited PYTHONPATH (hermes-agent numpy 2.4.3 shadows our rvc_env).
    clean_env = {k: v for k, v in os.environ.items() if k.upper() != "PYTHONPATH"}
    clean_env["PYTHONPATH"] = rvc_repo
    clean_env["RVC_DEVICE"] = RVC_DEVICE
    proc = subprocess.run(cmd, capture_output=True, text=True, cwd=rvc_repo, env=clean_env)
    if proc.returncode != 0 or not os.path.exists(out_path) or os.path.getsize(out_path) < 1024:
        raise RuntimeError(f"RVC infer failed: {proc.stderr[:500]}")
    return out_path


async def generate_voice(text: str, character: str, output_path: str,
                         narrator_voice: str = None, emotion: str = "neutral",
                         emphasis: list = None) -> dict:
    """Generate speech for `character` in their voice if trained, else narrator fallback.

    emotion: drives narrator prosody (SSML) + per-character RVC pitch.
    emphasis: list of (start, end) char offsets into `text` to stress in the narrator.
    Returns dict: {path, method: 'rvc'|'edge_tts', character, narrator, emotion}
    """
    from src.generation import emotion as EM
    narrator = narrator_voice or FALLBACK_NARRATOR.get(character, "en-US-GuyNeural")
    tmp_narrator = str(Path(output_path).with_suffix("")) + "_narrator.wav"

    # Step 1: author the line with Edge-TTS, applying emotion prosody.
    prosody = EM.edge_tts_args(emotion)
    await _edge_tts(text, narrator, tmp_narrator, prosody=prosody)

    # Step 2: convert to character voice if an RVC model is ready
    if rvc_model_ready(character):
        try:
            _rvc_convert(tmp_narrator, character, output_path)
            try:
                os.remove(tmp_narrator)
            except OSError:
                pass
            logger.info(f"Voice({character}): RVC conversion OK (emotion={emotion})")
            return {"path": output_path, "method": "rvc", "character": character,
                    "narrator": narrator, "emotion": emotion}
        except Exception as e:
            logger.warning(f"Voice({character}): RVC failed ({e}); falling back to Edge-TTS")
            if os.path.exists(tmp_narrator):
                os.replace(tmp_narrator, output_path)
            return {"path": output_path, "method": "edge_tts", "character": character,
                    "narrator": narrator, "emotion": emotion}

    # No model yet: use narrator directly
    os.replace(tmp_narrator, output_path)
    return {"path": output_path, "method": "edge_tts", "character": character,
            "narrator": narrator, "emotion": emotion}
