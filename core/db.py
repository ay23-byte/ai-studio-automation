import sqlite3, json
from core.config import DB_PATH

def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_conn()
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS videos (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        created_at  TEXT DEFAULT (datetime('now')),
        title       TEXT,
        format      TEXT,
        language    TEXT,
        category    TEXT,
        status      TEXT DEFAULT 'pending',
        script_json TEXT,
        video_path  TEXT,
        thumb_path  TEXT,
        youtube_id  TEXT,
        notes       TEXT
    );
    CREATE TABLE IF NOT EXISTS topics (
        id       INTEGER PRIMARY KEY AUTOINCREMENT,
        title    TEXT UNIQUE,
        category TEXT,
        used     INTEGER DEFAULT 0,
        approved INTEGER DEFAULT 1
    );
    """)
    conn.commit(); conn.close()

def save_video(title, fmt, lang, category, script):
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO videos (title,format,language,category,script_json) VALUES (?,?,?,?,?)",
        (title, fmt, lang, category, json.dumps(script, ensure_ascii=False)))
    vid_id = cur.lastrowid
    conn.commit(); conn.close()
    return vid_id

def update_video(vid_id, **kw):
    if not kw: return
    cols = ", ".join(f"{k}=?" for k in kw)
    conn = get_conn()
    conn.execute(f"UPDATE videos SET {cols} WHERE id=?", (*kw.values(), vid_id))
    conn.commit(); conn.close()

def list_videos(limit=50):
    conn = get_conn()
    rows = conn.execute("SELECT * FROM videos ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]

init_db()
