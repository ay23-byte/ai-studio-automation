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
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        created_at TEXT DEFAULT (datetime('now')),
        title TEXT, format TEXT, language TEXT, category TEXT,
        status TEXT DEFAULT 'pending', script_json TEXT, video_path TEXT,
        thumb_path TEXT, youtube_id TEXT, notes TEXT,
        approved INTEGER NOT NULL DEFAULT 0, scheduled_at TEXT,
        privacy_status TEXT DEFAULT 'unlisted', channel_profile_id INTEGER
    );
    CREATE TABLE IF NOT EXISTS topics (
        id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT UNIQUE,
        category TEXT, used INTEGER DEFAULT 0, approved INTEGER DEFAULT 1
    );
    CREATE TABLE IF NOT EXISTS channel_profiles (
        id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE,
        handle TEXT DEFAULT '', niche TEXT DEFAULT '',
        language TEXT DEFAULT 'hindi', category TEXT DEFAULT 'education',
        brand_voice TEXT DEFAULT '', created_at TEXT DEFAULT (datetime('now')),
        active INTEGER NOT NULL DEFAULT 1
    );
    """)
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(videos)")}
    migrations = {
        "approved": "INTEGER NOT NULL DEFAULT 0",
        "scheduled_at": "TEXT",
        "privacy_status": "TEXT DEFAULT 'unlisted'",
        "channel_profile_id": "INTEGER",
        "quality_report_path": "TEXT",
        "quality_score": "INTEGER",
        "quality_status": "TEXT",
    }
    for name, definition in migrations.items():
        if name not in columns:
            conn.execute(f"ALTER TABLE videos ADD COLUMN {name} {definition}")
    conn.commit()
    conn.close()

def save_video(title, fmt, lang, category, script, channel_profile_id=None):
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO videos (title,format,language,category,script_json,channel_profile_id,status) VALUES (?,?,?,?,?,?,?)",
        (title, fmt, lang, category, json.dumps(script, ensure_ascii=False), channel_profile_id, "script_ready"))
    vid_id = cur.lastrowid
    conn.commit()
    conn.close()
    return vid_id

def update_video(vid_id, **kw):
    if not kw:
        return
    allowed = {"title", "status", "video_path", "thumb_path", "youtube_id",
               "notes", "approved", "scheduled_at", "privacy_status", "channel_profile_id",
               "quality_report_path", "quality_score", "quality_status", "script_json"}
    unknown = set(kw) - allowed
    if unknown:
        raise ValueError(f"Unsupported video fields: {', '.join(sorted(unknown))}")
    cols = ", ".join(f"{k}=?" for k in kw)
    conn = get_conn()
    conn.execute(f"UPDATE videos SET {cols} WHERE id=?", (*kw.values(), vid_id))
    conn.commit()
    conn.close()

def list_videos(limit=100):
    conn = get_conn()
    rows = conn.execute(
        """SELECT videos.*, channel_profiles.name AS channel_name
           FROM videos LEFT JOIN channel_profiles
           ON videos.channel_profile_id=channel_profiles.id
           ORDER BY videos.created_at DESC, videos.id DESC LIMIT ?""", (limit,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]

def create_channel_profile(name, handle="", niche="", language="hindi",
                           category="education", brand_voice=""):
    name = (name or "").strip()
    if not name:
        raise ValueError("Channel name is required.")
    if language not in {"hindi", "english", "hinglish"}:
        raise ValueError("Language must be hindi, english, or hinglish.")
    conn = get_conn()
    try:
        cur = conn.execute(
            """INSERT INTO channel_profiles
               (name,handle,niche,language,category,brand_voice) VALUES (?,?,?,?,?,?)""",
            (name, handle.strip(), niche.strip(), language, category.strip(), brand_voice.strip()))
        conn.commit()
        return cur.lastrowid
    except sqlite3.IntegrityError as exc:
        raise ValueError("A channel profile with this name already exists.") from exc
    finally:
        conn.close()

def list_channel_profiles(active_only=False):
    conn = get_conn()
    sql = "SELECT * FROM channel_profiles"
    if active_only:
        sql += " WHERE active=1"
    rows = conn.execute(sql + " ORDER BY name COLLATE NOCASE").fetchall()
    conn.close()
    return [dict(row) for row in rows]

def update_channel_profile(profile_id, **kw):
    allowed = {"name", "handle", "niche", "language", "category", "brand_voice", "active"}
    unknown = set(kw) - allowed
    if unknown:
        raise ValueError("Unsupported channel fields: " + ", ".join(sorted(unknown)))
    if not kw:
        return
    if "name" in kw:
        kw["name"] = kw["name"].strip()
        if not kw["name"]:
            raise ValueError("Channel name cannot be empty.")
    if "language" in kw and kw["language"] not in {"hindi", "english", "hinglish"}:
        raise ValueError("Language must be hindi, english, or hinglish.")
    conn = get_conn()
    try:
        assignments = ", ".join(f"{key}=?" for key in kw)
        conn.execute(f"UPDATE channel_profiles SET {assignments} WHERE id=?",
                     (*kw.values(), profile_id))
        conn.commit()
    except sqlite3.IntegrityError as exc:
        raise ValueError("A channel profile with this name already exists.") from exc
    finally:
        conn.close()

init_db()
