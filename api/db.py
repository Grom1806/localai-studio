"""SQLite metadata store. Media files stay on disk; only paths are stored here."""
import json
import sqlite3
import threading
import time

from settings import DB_PATH

_lock = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS generations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created TEXT NOT NULL,
    type TEXT NOT NULL,              -- image | video | audio
    engine TEXT,                     -- backend name (comfyui, chatterbox, ...)
    model TEXT,
    prompt TEXT,
    negative TEXT,
    seed INTEGER,
    params TEXT,                     -- JSON
    inputs TEXT,                     -- JSON: reference files used
    outputs TEXT,                    -- JSON list of file paths
    status TEXT,
    error TEXT,
    seconds REAL,
    peak_vram_mb INTEGER
);
CREATE INDEX IF NOT EXISTS ix_gen_type ON generations(type, id);
CREATE TABLE IF NOT EXISTS avatars (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    image_path TEXT NOT NULL,        -- reference portrait (relative path)
    video_path TEXT,                 -- optional reference video
    voice_id INTEGER,                -- default saved voice
    language TEXT,
    preview_path TEXT,
    notes TEXT,
    created TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS voices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    ref_path TEXT NOT NULL,
    language TEXT,
    transcript TEXT,
    notes TEXT,
    created TEXT NOT NULL
);
"""


def _conn():
    c = sqlite3.connect(DB_PATH, timeout=10, check_same_thread=False)
    c.row_factory = sqlite3.Row
    return c


def init():
    with _lock, _conn() as c:
        c.execute("PRAGMA journal_mode=WAL")
        c.executescript(SCHEMA)
    _seed_default_voices()


def _seed_default_voices():
    """Fresh installs get the built-in "Default female/male (RU)" voices (used by the 'Default AI voice' option)."""
    from settings import VOICES
    f = VOICES / "default_voices.json"
    if not f.exists():
        return
    have = {v["name"] for v in list_voices()}
    for v in json.loads(f.read_text(encoding="utf-8")):
        if v["name"] not in have and (VOICES.parent / v["ref_path"]).exists():
            add_voice(v["name"], v["ref_path"], v.get("language"), v.get("transcript"), v.get("notes"))


def _row(r):
    if r is None:
        return None
    d = dict(r)
    for k in ("params", "inputs", "outputs"):
        if k in d and d[k]:
            try:
                d[k] = json.loads(d[k])
            except Exception:
                pass
    return d


def now():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def add_generation(**f) -> int:
    for k in ("params", "inputs", "outputs"):
        if k in f and not isinstance(f[k], str):
            f[k] = json.dumps(f[k], ensure_ascii=False)
    f.setdefault("created", now())
    cols = ",".join(f)
    with _lock, _conn() as c:
        cur = c.execute(f"INSERT INTO generations ({cols}) VALUES ({','.join('?' * len(f))})", list(f.values()))
        return cur.lastrowid


def update_generation(gid: int, **f):
    for k in ("params", "inputs", "outputs"):
        if k in f and not isinstance(f[k], str):
            f[k] = json.dumps(f[k], ensure_ascii=False)
    sets = ",".join(f"{k}=?" for k in f)
    with _lock, _conn() as c:
        c.execute(f"UPDATE generations SET {sets} WHERE id=?", [*f.values(), gid])


def get_generation(gid: int):
    with _conn() as c:
        return _row(c.execute("SELECT * FROM generations WHERE id=?", (gid,)).fetchone())


def list_generations(type_: str | None = None, q: str | None = None, limit=200, offset=0, status="done"):
    sql, args = "SELECT * FROM generations WHERE 1=1", []
    if status:
        sql += " AND status=?"
        args.append(status)
    if type_:
        sql += " AND type=?"
        args.append(type_)
    if q:
        sql += " AND (prompt LIKE ? OR model LIKE ?)"
        args += [f"%{q}%", f"%{q}%"]
    sql += " ORDER BY id DESC LIMIT ? OFFSET ?"
    args += [limit, offset]
    with _conn() as c:
        return [_row(r) for r in c.execute(sql, args).fetchall()]


def delete_generation(gid: int):
    with _lock, _conn() as c:
        c.execute("DELETE FROM generations WHERE id=?", (gid,))


def stats():
    with _conn() as c:
        rows = c.execute("SELECT type, COUNT(*) n, AVG(seconds) s, AVG(peak_vram_mb) v FROM generations "
                         "WHERE status='done' GROUP BY type").fetchall()
        return {r["type"]: {"count": r["n"], "avg_seconds": r["s"], "avg_peak_vram_mb": r["v"]} for r in rows}


# ── avatars ──
def add_avatar(**f) -> int:
    f.setdefault("created", now())
    with _lock, _conn() as c:
        cur = c.execute(f"INSERT INTO avatars ({','.join(f)}) VALUES ({','.join('?' * len(f))})", list(f.values()))
        return cur.lastrowid


def list_avatars():
    with _conn() as c:
        return [dict(r) for r in c.execute("SELECT * FROM avatars ORDER BY name COLLATE NOCASE").fetchall()]


def get_avatar(aid: int):
    with _conn() as c:
        r = c.execute("SELECT * FROM avatars WHERE id=?", (aid,)).fetchone()
        return dict(r) if r else None


def update_avatar(aid: int, **f):
    if f:
        with _lock, _conn() as c:
            c.execute(f"UPDATE avatars SET {','.join(k + '=?' for k in f)} WHERE id=?", [*f.values(), aid])


def delete_avatar(aid: int):
    with _lock, _conn() as c:
        c.execute("DELETE FROM avatars WHERE id=?", (aid,))


# ── voices ──
def add_voice(name, ref_path, language=None, transcript=None, notes=None) -> int:
    with _lock, _conn() as c:
        cur = c.execute("INSERT INTO voices (name, ref_path, language, transcript, notes, created) VALUES (?,?,?,?,?,?)",
                        (name, ref_path, language, transcript, notes, now()))
        return cur.lastrowid


def list_voices():
    with _conn() as c:
        return [dict(r) for r in c.execute("SELECT * FROM voices ORDER BY name COLLATE NOCASE").fetchall()]


def get_voice(vid: int):
    with _conn() as c:
        r = c.execute("SELECT * FROM voices WHERE id=?", (vid,)).fetchone()
        return dict(r) if r else None


def update_voice(vid: int, **f):
    if not f:
        return
    sets = ",".join(f"{k}=?" for k in f)
    with _lock, _conn() as c:
        c.execute(f"UPDATE voices SET {sets} WHERE id=?", [*f.values(), vid])


def delete_voice(vid: int):
    with _lock, _conn() as c:
        c.execute("DELETE FROM voices WHERE id=?", (vid,))


def voice_ref_in_use(ref_path: str, exclude_id: int) -> bool:
    with _conn() as c:
        return c.execute("SELECT 1 FROM voices WHERE ref_path=? AND id<>?", (ref_path, exclude_id)).fetchone() is not None
