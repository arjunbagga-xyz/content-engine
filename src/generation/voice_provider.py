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
RVC_RETRIES = 6             # this fork is flaky (cli intermittently returns rc=0 w/ no output);
                             # 6 attempts cycle gpu+index -> cpu+index -> cpu+noindex so each
                             # mode gets 2 tries before giving up
RVC_F0_STD_FLOOR = 12.0     # Hz: F0 std below this => output too flat/generic => retry

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


def _rvc_f0_stats(wav_path: str):
    """Return (f0_min, f0_max, f0_mean, f0_std) Hz for an RVC output wav.

    Dependency-free: reads the wav with the stdlib `wave` module + numpy autocorrelation
    (librosa/soundfile aren't reliably importable in rvc_env, so avoid them here).
    Used by the quality gate to detect flat/generic RVC outputs (low F0 std => the model
    didn't reshape the voice => retry for a better stochastic draw). Never raises into the
    caller on failure — returns zeros so the caller simply skips retrying that attempt.
    """
    try:
        import wave
        import numpy as np
        with wave.open(wav_path, "rb") as wf:
            nchannels = wf.getnchannels()
            sampwidth = wf.getsampwidth()
            nframes = wf.getnframes()
            sr = wf.getframerate()
            raw = wf.readframes(nframes)
        # decode PCM to float mono
        if sampwidth == 2:
            dtype = np.int16
        elif sampwidth == 4:
            dtype = np.int32
        else:
            dtype = np.uint8
        arr = np.frombuffer(raw, dtype=dtype).astype(np.float64)
        if nchannels > 1:
            arr = arr.reshape(-1, nchannels).mean(axis=1)
        if sampwidth == 2:
            arr /= 32768.0
        elif sampwidth == 4:
            arr /= 2147483648.0
        # autocorrelation F0 over short frames
        frame = int(0.025 * sr)
        hop = int(0.010 * sr)
        f0s = []
        fmin, fmax = 80.0, 670.0
        fmin_lag = int(sr / fmax)
        fmax_lag = int(sr / fmin)
        for off in range(0, max(1, len(arr) - frame), hop):
            seg = arr[off:off + frame]
            seg = seg - seg.mean()
            if seg.std() < 1e-4:
                continue
            ac = np.correlate(seg, seg, "full")[len(seg) - 1:]
            ac /= ac[0]
            # restrict lag search to [fmin_lag, fmax_lag]
            lo = min(len(ac), fmin_lag)
            hi = min(len(ac), fmax_lag + 1)
            if hi <= lo:
                continue
            lag = lo + int(np.argmax(ac[lo:hi]))
            if ac[lag] > 0.3:  # voiced
                f0s.append(sr / lag)
        f0 = np.array(f0s)
        if f0.size < 2:
            return 0.0, 0.0, 0.0, 0.0
        return float(f0.min()), float(f0.max()), float(f0.mean()), float(f0.std())
    except Exception as e:
        logger.debug(f"F0 stats failed for {wav_path}: {e}")
        return 0.0, 0.0, 0.0, 0.0



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

    logger.info(f"RVC convert: {character} ({'with index ' + str(index) if index else 'no index'})")

    # PERSISTENT CUDA WORKER. The old code spawned a fresh rvc_repo/infer/cli.py
    # subprocess per segment; every spawn reloaded HuBERT+RMVPE+model+index AND
    # re-allocated GPU memory, and on the 4GB 1650 that per-call CUDA alloc crashed
    # intermittently (rc=0, no output) -> killed the reel. The sandbox proof showed
    # a persistent worker (model loaded ONCE, reuse VC) converts 10/10 segments on
    # the 1650 at ~27s/seg with zero flakes. The fork's config.py already tunes
    # fp32 + x_pad/x_query/x_center/x_max for the 1650's 4GB, so CUDA:0 is stable.
    try:
        from src.generation import rvc_worker
        ok, out = rvc_worker.batch_convert(
            character, [(narrator_wav, out_path)], pitch=pitch
        )[0]
        if not ok or not os.path.exists(out_path) or os.path.getsize(out_path) < 1024:
            raise RuntimeError("RVC worker returned no usable output")
    except Exception as e:
        # Loud failure, no silent Edge-TTS fallback (per directive: we must never
        # publish a generic narrator voice in place of a character voice).
        raise RuntimeError(f"RVC infer failed for {character}: {e}")

    # Quality gate: RVC is nondeterministic; a "flat/generic" result has a thin F0
    # range. With a persistent worker this is cheap to re-check; if it's flat we
    # still return the file (a flat character voice beats a wrong narrator voice),
    # but log it so we can see quality drift.
    f0_min, f0_max, f0_mean, f0_std = _rvc_f0_stats(out_path)
    logger.info(f"RVC {character}: F0 min={f0_min:.1f} max={f0_max:.1f} "
                f"mean={f0_mean:.1f} std={f0_std:.1f}")
    if f0_std < RVC_F0_STD_FLOOR:
        logger.warning(f"RVC {character}: output F0 std {f0_std:.1f} below floor "
                       f"{RVC_F0_STD_FLOOR} (flat/generic); using anyway (no fallback).")
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

    # Step 2: convert to character voice. RVC is MANDATORY when a model exists for
    # this character — we must NEVER silently fall back to the generic Edge-TTS
    # narrator (that ships a robotic, non-character voice to Instagram). If RVC is
    # configured but fails, we raise so the reel FAILS LOUDLY instead of publishing
    # a wrong voice. Only accounts with NO RVC model at all may use the narrator
    # (a deliberate "not trained yet" state, logged loudly).
    if rvc_model_ready(character):
        _rvc_convert(tmp_narrator, character, output_path)  # raises on failure (no fallback)
        try:
            os.remove(tmp_narrator)
        except OSError:
            pass
        logger.info(f"Voice({character}): RVC conversion OK (emotion={emotion})")
        return {"path": output_path, "method": "rvc", "character": character,
                "narrator": narrator, "emotion": emotion}

    # No RVC model configured for this character: narrator fallback is allowed
    # (account not trained yet), but log it loudly so it is never silent.
    logger.warning(
        f"Voice({character}): NO RVC model configured — using Edge-TTS narrator "
        f"'{narrator}' (NOT a character voice). Train/fix the RVC model before publishing."
    )
    os.replace(tmp_narrator, output_path)
    return {"path": output_path, "method": "edge_tts", "character": character,
            "narrator": narrator, "emotion": emotion}
