import sqlite3
import json
import os
from datetime import datetime

DB_DIR = os.environ.get("SRE_DB_DIR", "./data")
os.makedirs(DB_DIR, exist_ok=True)
DB_PATH = os.path.join(DB_DIR, "sre_platform.db")

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with get_db() as conn:
        # Settings table
        conn.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)
        # Incidents table
        conn.execute("""
            CREATE TABLE IF NOT EXISTS incidents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                status TEXT NOT NULL, -- pending, approved, rejected, resolved, failed
                title TEXT NOT NULL,
                service TEXT NOT NULL,
                alert_payload TEXT,
                logs TEXT,
                ai_analysis TEXT,
                proposed_command TEXT,
                action_output TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        conn.commit()

def get_setting(key: str, default: str = "") -> str:
    try:
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute("SELECT value FROM settings WHERE key = ?", (key,))
            row = cur.fetchone()
            return row["value"] if row else default
    except Exception:
        return default

def set_setting(key: str, value: str):
    with get_db() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
            (key, value)
        )
        conn.commit()

def get_all_settings() -> dict:
    try:
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute("SELECT key, value FROM settings")
            return {row["key"]: row["value"] for row in cur.fetchall()}
    except Exception:
        return {}

def create_incident(title: str, service: str, alert_payload: dict, logs: str, ai_analysis: str, proposed_command: str) -> int:
    now = datetime.now().isoformat()
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO incidents 
            (status, title, service, alert_payload, logs, ai_analysis, proposed_command, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "pending" if proposed_command else "resolved",
                title,
                service,
                json.dumps(alert_payload),
                logs,
                ai_analysis,
                proposed_command,
                now,
                now
            )
        )
        conn.commit()
        return cur.lastrowid

def update_incident_status(incident_id: int, status: str, action_output: str = None):
    now = datetime.now().isoformat()
    with get_db() as conn:
        if action_output is not None:
            conn.execute(
                "UPDATE incidents SET status = ?, action_output = ?, updated_at = ? WHERE id = ?",
                (status, action_output, now, incident_id)
            )
        else:
            conn.execute(
                "UPDATE incidents SET status = ?, updated_at = ? WHERE id = ?",
                (status, now, incident_id)
            )
        conn.commit()

def get_all_incidents() -> list:
    try:
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM incidents ORDER BY id DESC")
            return [dict(row) for row in cur.fetchall()]
    except Exception:
        return []

def get_incident(incident_id: int) -> dict:
    try:
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM incidents WHERE id = ?", (incident_id,))
            row = cur.fetchone()
            return dict(row) if row else None
    except Exception:
        return None

# Initial database migration
init_db()
