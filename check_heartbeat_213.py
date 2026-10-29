import sqlite3, sys
path = r"D:\Open Projects\Content Engine\data\content_engine.sqlite3"
conn = sqlite3.connect(path)
c = conn.cursor()
c.execute("SELECT id, state, pid, heartbeat_at, media_path, actual_posted_time FROM content_queue WHERE id=213;")
print(c.fetchall())
