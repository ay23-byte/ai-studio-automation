"""SQLite job store.

Safety properties (see docs/ARCHITECTURE.md, "Database safety"):

* Additive migrations only. No table or column is ever dropped or rewritten.
* Before the first schema change on an existing database a timestamped copy is
  written to ``db/backups/`` using SQLite's online-backup API.
* Every write runs inside a transaction that commits or rolls back as a unit;
  connections are always closed, including on error paths.
* ``busy_timeout`` lets the dashboard and a running build share the file.
* Field names in dynamic UPDATEs are checked against an allow-list.
"""
import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from core import config as _cfg
from core.config import DB_PATH

SCHEMA_VERSION = 3
FORMAT_ALIASES = {"long": "longform", "short": "shorts", "news": "news_short"}

_VIDEO_COLUMNS = {
    "approved": "INTEGER NOT NULL DEFAULT 0",
    "scheduled_at": "TEXT",
    "privacy_status": "TEXT DEFAULT 'unlisted'",
    "channel_profile_id": "INTEGER",
    "quality_report_path": "TEXT",
    "quality_score": "INTEGER",
    "quality_status": "TEXT",
    "planned_publish_at": "TEXT",
    # v2+: orchestration and duplicate prevention
    "pipeline": "TEXT",
    "topic_key": "TEXT",
    "source_ref": "TEXT",
    "error": "TEXT",
    "attempts": "INTEGER NOT NULL DEFAULT 0",
    "updated_at": "TEXT",
    "uploaded_at": "TEXT",
}

_UPDATABLE_VIDEO_FIELDS = {
    "title", "status", "video_path", "thumb_path", "youtube_id",
    "notes", "approved", "scheduled_at", "privacy_status", "channel_profile_id",
    "quality_report_path", "quality_score", "quality_status", "planned_publish_at",
    "script_json", "pipeline", "topic_key", "source_ref", "error", "attempts",
    "uploaded_at", "format", "language", "category",
}


def _db_path() -> Path:
    # Read through the module attribute so tests can redirect ``core.db.DB_PATH``.
    return Path(globals().get("DB_PATH") or _cfg.DB_PATH)


def normalize_format(fmt) -> str:
    value = str(fmt or "").strip().lower()
    return FORMAT_ALIASES.get(value, value)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def get_conn():
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA foreign_keys=ON")
    if _cfg.env_flag("AI_STUDIO_DB_WAL"):
        try:
            conn.execute("PRAGMA journal_mode=WAL")
        except sqlite3.DatabaseError:
            pass
    return conn


@contextmanager
def transaction():
    """Yield a connection; commit on success, roll back on any exception."""
    conn = get_conn()
    try:
        yield conn
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


def backup_database(reason="manual") -> Path | None:
    """Write a consistent timestamped copy of the database; never overwrites."""
    source = _db_path()
    if not source.is_file() or source.stat().st_size == 0:
        return None
    backup_dir = source.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    safe_reason = "".join(ch if ch.isalnum() else "-" for ch in str(reason))[:40]
    target = backup_dir / f"{source.stem}-{stamp}-{safe_reason}{source.suffix}"
    src = sqlite3.connect(str(source), timeout=30)
    try:
        dst = sqlite3.connect(str(target))
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()
    # Keep the newest backups only; pruning touches backups, never the live file.
    keep = max(3, int(os.getenv("AI_STUDIO_DB_BACKUPS_KEEP", "20") or 20))
    backups = sorted(backup_dir.glob(f"{source.stem}-*{source.suffix}"))
    for old in backups[:-keep]:
        try:
            old.unlink()
        except OSError:
            pass
    return target


def integrity_check() -> str:
    conn = get_conn()
    try:
        return str(conn.execute("PRAGMA integrity_check").fetchone()[0])
    finally:
        conn.close()


def _needs_migration(conn) -> bool:
    tables = {row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if not tables:
        return False  # brand-new file: nothing to protect
    required = {"videos", "topics", "channel_profiles", "job_stages", "publish_ledger", "schema_meta"}
    if not required.issubset(tables):
        return True
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(videos)")}
    return not set(_VIDEO_COLUMNS).issubset(columns)


def init_db():
    path = _db_path()
    existed = path.is_file() and path.stat().st_size > 0
    conn = get_conn()
    try:
        if existed and _needs_migration(conn):
            conn.close()
            backup_database("pre-migration")
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
        CREATE TABLE IF NOT EXISTS job_stages (
            job_id INTEGER NOT NULL, stage TEXT NOT NULL,
            status TEXT NOT NULL, attempt INTEGER NOT NULL DEFAULT 1,
            started_at TEXT, finished_at TEXT, duration_seconds REAL,
            input_hash TEXT, detail_json TEXT, error TEXT,
            PRIMARY KEY (job_id, stage)
        );
        CREATE TABLE IF NOT EXISTS publish_ledger (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT DEFAULT (datetime('now')),
            kind TEXT NOT NULL, dedupe_key TEXT NOT NULL,
            title TEXT, source_ref TEXT, job_id INTEGER,
            youtube_id TEXT, privacy_status TEXT, status TEXT, meta_json TEXT,
            UNIQUE (kind, dedupe_key)
        );
        CREATE TABLE IF NOT EXISTS schema_meta (key TEXT PRIMARY KEY, value TEXT);
        """)
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(videos)")}
        for name, definition in _VIDEO_COLUMNS.items():
            if name not in columns:
                conn.execute(f"ALTER TABLE videos ADD COLUMN {name} {definition}")
        conn.executescript("""
        CREATE INDEX IF NOT EXISTS idx_videos_status ON videos(status);
        CREATE INDEX IF NOT EXISTS idx_videos_topic_key ON videos(topic_key);
        CREATE INDEX IF NOT EXISTS idx_videos_youtube_id ON videos(youtube_id);
        CREATE INDEX IF NOT EXISTS idx_ledger_youtube ON publish_ledger(youtube_id);
        """)
        conn.execute(
            "INSERT INTO schema_meta(key, value) VALUES('schema_version', ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (str(SCHEMA_VERSION),))
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


def save_video(title, fmt, lang, category, script, channel_profile_id=None,
               pipeline=None, topic_key=None, source_ref=None):
    fmt = normalize_format(fmt)
    with transaction() as conn:
        cur = conn.execute(
            "INSERT INTO videos (title,format,language,category,script_json,"
            "channel_profile_id,status,pipeline,topic_key,source_ref,updated_at,privacy_status) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (title, fmt, lang, category, json.dumps(script, ensure_ascii=False),
             channel_profile_id, "script_ready", pipeline or fmt, topic_key,
             source_ref, _now(), "private"))  # legacy column default was 'unlisted'
        return cur.lastrowid


def update_video(vid_id, **kw):
    if not kw:
        return
    unknown = set(kw) - _UPDATABLE_VIDEO_FIELDS
    if unknown:
        raise ValueError(f"Unsupported video fields: {', '.join(sorted(unknown))}")
    if "format" in kw:
        kw["format"] = normalize_format(kw["format"])
    values = dict(kw)
    values["updated_at"] = _now()
    cols = ", ".join(f"{k}=?" for k in values)
    with transaction() as conn:
        conn.execute(f"UPDATE videos SET {cols} WHERE id=?", (*values.values(), vid_id))


def get_video(vid_id):
    conn = get_conn()
    try:
        row = conn.execute("SELECT * FROM videos WHERE id=?", (vid_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def list_videos(limit=100):
    conn = get_conn()
    try:
        rows = conn.execute(
            """SELECT videos.*, channel_profiles.name AS channel_name
               FROM videos LEFT JOIN channel_profiles
               ON videos.channel_profile_id=channel_profiles.id
               ORDER BY videos.created_at DESC, videos.id DESC LIMIT ?""", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ---------------------------------------------------------------- job stages
def record_stage(job_id, stage, status, *, attempt=1, started_at=None, finished_at=None,
                 duration_seconds=None, input_hash=None, detail=None, error=None):
    """Upsert one checkpoint row for a job stage."""
    with transaction() as conn:
        conn.execute(
            """INSERT INTO job_stages
               (job_id, stage, status, attempt, started_at, finished_at,
                duration_seconds, input_hash, detail_json, error)
               VALUES (?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(job_id, stage) DO UPDATE SET
                 status=excluded.status, attempt=excluded.attempt,
                 started_at=COALESCE(excluded.started_at, job_stages.started_at),
                 finished_at=excluded.finished_at,
                 duration_seconds=excluded.duration_seconds,
                 input_hash=COALESCE(excluded.input_hash, job_stages.input_hash),
                 detail_json=excluded.detail_json, error=excluded.error""",
            (int(job_id), str(stage), str(status), int(attempt), started_at, finished_at,
             duration_seconds, input_hash,
             json.dumps(detail, ensure_ascii=False) if detail is not None else None,
             str(error)[:1000] if error else None))


def list_stages(job_id):
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM job_stages WHERE job_id=? ORDER BY started_at, rowid", (int(job_id),)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ------------------------------------------------------------ publish ledger
def ledger_get(kind, dedupe_key):
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM publish_ledger WHERE kind=? AND dedupe_key=?",
            (str(kind), str(dedupe_key))).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def ledger_put(kind, dedupe_key, *, title="", source_ref="", job_id=None,
               youtube_id=None, privacy_status=None, status="recorded", meta=None):
    """Insert or update a ledger entry; returns the stored row."""
    with transaction() as conn:
        conn.execute(
            """INSERT INTO publish_ledger
               (kind, dedupe_key, title, source_ref, job_id, youtube_id,
                privacy_status, status, meta_json)
               VALUES (?,?,?,?,?,?,?,?,?)
               ON CONFLICT(kind, dedupe_key) DO UPDATE SET
                 title=COALESCE(NULLIF(excluded.title,''), publish_ledger.title),
                 source_ref=COALESCE(NULLIF(excluded.source_ref,''), publish_ledger.source_ref),
                 job_id=COALESCE(excluded.job_id, publish_ledger.job_id),
                 youtube_id=COALESCE(excluded.youtube_id, publish_ledger.youtube_id),
                 privacy_status=COALESCE(excluded.privacy_status, publish_ledger.privacy_status),
                 status=excluded.status,
                 meta_json=COALESCE(excluded.meta_json, publish_ledger.meta_json)""",
            (str(kind), str(dedupe_key), str(title or ""), str(source_ref or ""), job_id,
             youtube_id, privacy_status, str(status),
             json.dumps(meta, ensure_ascii=False) if meta is not None else None))
    return ledger_get(kind, dedupe_key)


def ledger_list(limit=500):
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM publish_ledger ORDER BY id DESC LIMIT ?", (int(limit),)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ----------------------------------------------------------- channel profiles
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
        conn.rollback()
        raise ValueError("A channel profile with this name already exists.") from exc
    finally:
        conn.close()


def list_channel_profiles(active_only=False):
    conn = get_conn()
    try:
        sql = "SELECT * FROM channel_profiles"
        if active_only:
            sql += " WHERE active=1"
        rows = conn.execute(sql + " ORDER BY name COLLATE NOCASE").fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


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
        conn.rollback()
        raise ValueError("A channel profile with this name already exists.") from exc
    finally:
        conn.close()


init_db()
