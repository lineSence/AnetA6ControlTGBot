from __future__ import annotations
import asyncio
import os
import re
import sqlite3
from datetime import datetime

ERROR_PATTERNS = (
    re.compile(r"^!!\s*(.+)$"),
    re.compile(r"(?i)^error[: ]+(.+)$"),
    re.compile(r"(?i)^exception[: ]+(.+)$"),
    re.compile(r"(?i)^shutdown[: ]+(.+)$"),
    re.compile(r"(?i).*Move exceeds maximum extrusion.*"),
)

def _connect(path):
    db = sqlite3.connect(path, timeout=10)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("""
        CREATE TABLE IF NOT EXISTS errors (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            source TEXT NOT NULL,
            state TEXT,
            filename TEXT,
            message TEXT NOT NULL,
            details TEXT,
            acknowledged INTEGER NOT NULL DEFAULT 0
        )
    """)
    db.commit()
    return db

def register(cfg, source, message, details="", state=None, filename=None):
    os.makedirs(os.path.dirname(cfg.error_db) or ".", exist_ok=True)
    text = str(message or "Неизвестная ошибка").strip()
    with _connect(cfg.error_db) as db:
        cur = db.execute(
            "INSERT INTO errors(ts,source,state,filename,message,details) VALUES(?,?,?,?,?,?)",
            (
                datetime.now().astimezone().isoformat(timespec="seconds"),
                str(source),
                state,
                filename,
                text,
                str(details or "").strip()[-20000:],
            ),
        )
        db.commit()
        return cur.lastrowid

async def register_async(cfg, source, message, details="", state=None, filename=None):
    return await asyncio.to_thread(register, cfg, source, message, details, state, filename)

def recent(cfg, limit=10):
    with _connect(cfg.error_db) as db:
        return db.execute(
            "SELECT id,ts,source,state,filename,message,details,acknowledged "
            "FROM errors ORDER BY id DESC LIMIT ?",
            (max(1, min(int(limit), 50)),),
        ).fetchall()

def get(cfg, error_id):
    with _connect(cfg.error_db) as db:
        return db.execute(
            "SELECT id,ts,source,state,filename,message,details,acknowledged "
            "FROM errors WHERE id=?",
            (int(error_id),),
        ).fetchone()

def extract_klippy_error(text):
    for line in reversed([x.strip() for x in (text or "").splitlines() if x.strip()]):
        for pat in ERROR_PATTERNS:
            m = pat.search(line)
            if m:
                return m.group(1).strip() if m.groups() else line
    return None

def read_klippy_tail(path="/tmp/klippy.log", lines=180):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return "".join(f.readlines()[-max(20, int(lines)):])
    except Exception:
        return ""
