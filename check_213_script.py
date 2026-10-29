import sqlite3, os, sys
path = "data/content_engine.sqlite3"
print("DB path:", path, "exists:", os.path.exists(path), "size:", os.path.getsize(path) if os.path.exists(path) else 0)
if not os.path.exists(path):
    sys.exit(1)
conn = sqlite3.connect(path)
c = conn.cursor()
c.execute("SELECT name FROM sqlite_master WHERE type='table';")
print("Tables:", [r[0] for r in c.fetchall()])
for t in ['content_queue','scheduled_jobs','posts','jobs','pipeline_state']:
    try:
        c.execute(f"SELECT sql FROM sqlite_master WHERE type='table' AND name='{t}'")
        schema = c.fetchone()
        print(f"\n=== {t} === schema exists: {schema is not None}")
        if schema:
            print("Schema:", schema[0][:300])
        # Try selecting rows mentioning 213
        try:
            c.execute(f"SELECT * FROM {t}")
            for r in c.fetchall():
                s = str(r)
                if '213' in s:
                    print(f"MATCH in {t}:", r)
        except Exception as e2:
            print(f"SELECT * FROM {t} failed:", e2)
    except Exception as e:
        print(f"{t} error: {e}")
