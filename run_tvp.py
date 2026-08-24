import asyncio, sys, os, time
sys.path.insert(0, r"D:\Open Projects\Content Engine")
from src.generation.sprite_reactor import SpriteReactor

async def m():
    t0 = time.time()
    # Generic, YAML-driven. Topic auto-picked from tate_vs_peppa.topic_universe
    # unless overridden. Trend scout / memory can supply topic later.
    r = await SpriteReactor.produce_account_debate(
        "tate_vs_peppa",
        "outputs/tvp_reel_2.mp4",
        topic=None,          # auto from topic_universe
        num_turns=None,      # auto from seg_range [8,16]
        sprite_scale=0.35)
    print("TVP DONE account=%s topic=%s turns=%d path=%s elapsed=%.1fs" % (
        r.get("account_id"), r.get("topic"), len(r["turns"]), r["path"], time.time()-t0), flush=True)
    for t in r["turns"]:
        tag = "[CAMEO]" if t.get("cameo") else ""
        print("  %-7s %s %s" % (t["speaker"], tag, t["text"][:80]), flush=True)

if __name__ == "__main__":
    asyncio.run(m())
