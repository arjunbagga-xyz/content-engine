"""Storytime pipeline - AI-generated story with narration."""
import os, sys, json, logging, subprocess, tempfile, shutil
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
logger = logging.getLogger("content_engine.pipeline.storytime")

PROJECT_ROOT = Path(__file__).resolve().parents[3]
OUTPUT_DIR = PROJECT_ROOT / "outputs"
W, H, FPS = 608, 1080, 45


def generate_story(char, topic, paragraphs=4):
    from src.generation.pipeline_impl.llm_helper import generate_text_sync
    prompt = (
        f"Write a short story about {topic} from the perspective of {char['name']}.\n"
        f"{char['name']} persona: {char['persona']}\n\n"
        "Rules for storytime style:\n"
        "- First person narrative: I remember when...\n"
        "- Conversational, like telling a friend\n"
        "- Funny, slightly exaggerated\n"
        "- Include setup, buildup, punchline ending\n"
        "- Each paragraph is 2-4 sentences\n"
        "- End with moral or funny realization\n\n"
        "Output JSON array:\n"
        '[{"text": "paragraph 1..."}, ...]'
    )
    text = generate_text_sync(prompt, 0.8)
    try:
        s, e = text.find('['), text.rfind(']') + 1
        if s >= 0 and e > 0:
            return json.loads(text[s:e])
    except:
        pass
    return [
        {"text": f"So I was thinking about {topic} the other day"},
        {"text": "and I realized something absolutely wild"},
        {"text": "like you would not believe what happened next"},
        {"text": "moral of the story: never underestimate the situation"},
    ]


def gen_audio(text, voice, out):
    import asyncio, edge_tts
    async def _g():
        await edge_tts.Communicate(text, voice).save(out)
    asyncio.run(_g())


def concat_audio(files, out):
    if len(files) == 1:
        shutil.copy(files[0], out)
        return
    inp = []
    filt = ""
    for i, f in enumerate(files):
        inp.extend(["-i", str(f)])
        filt += f"[{i}:a]"
    filt += f"concat=n={len(files)}:v=0:a=1[a]"
    subprocess.run(["ffmpeg", "-y"] + inp + ["-filter_complex", filt, "-map", "[a]", "-acodec", "pcm_s16le", str(out)], capture_output=True, timeout=60)


def render(story, audio_path, out):
    dur = sum(len(p["text"].split()) * 0.3 + 1.0 for p in story)
    dur = max(dur, 10)
    
    draws = []
    t = 0.5
    for i, p in enumerate(story):
        txt = p["text"].replace("'", "_").replace(":", "_")
        y_pos = 200 + (i % 4) * 120
        dur_line = len(p["text"].split()) * 0.3 + 1.0
        draws.append(
            f"drawtext=text={txt}:fontfile='C\\:/Windows/Fonts/arial.ttf':fontcolor=white:"
            f"box=1:boxcolor=#1a1a2e@0.9:boxborderw=15:x=60:y={y_pos}:fontsize=20:"
            f"enable='between(t\\,{t}\\,{t+dur_line})'"
        )
        t += dur_line + 0.5
    
    vf = ",".join(draws) if draws else "null"
    src = f"color=c=#0f0f23:s={W}x{H}:d={dur}:r={FPS}"
    cmd = ["ffmpeg", "-y", "-f", "lavfi", "-i", src, "-i", str(audio_path),
           "-vf", vf, "-c:v", "libx264", "-preset", "fast", "-crf", "23",
           "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k", "-shortest",
           "-movflags", "+faststart", str(out)]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        logger.error("ffmpeg: %s", r.stderr[-300:])
    return str(out)


def run_pipeline(char, topic, name, paragraphs=4):
    logger.info("Storytime: %s on %s", char["name"], topic)
    story = generate_story(char, topic, paragraphs)
    if not story:
        return None
    tmp = Path(tempfile.mkdtemp())
    afs = []
    for i, p in enumerate(story):
        fp = str(tmp / f"p{i}.wav")
        gen_audio(p["text"], char["tts_voice"], fp)
        afs.append(Path(fp))
    ap = tmp / "c.wav"
    concat_audio(afs, ap)
    out = str(OUTPUT_DIR / f"{name}.mp4")
    render(story, ap, out)
    shutil.rmtree(tmp, ignore_errors=True)
    return out


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run_pipeline(
        {"name": "Goku", "persona": "cheerful naive", "tts_voice": "en-US-GuyNeural"},
        "the time I tried to cook ramen in a microwave", "storytime_final"
    )
