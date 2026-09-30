import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id() -> str:
    return uuid.uuid4().hex


class Database:
    def __init__(self, path: Path):
        self.path = path
        with self.connection() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS assets (
                    id TEXT PRIMARY KEY, kind TEXT NOT NULL, name TEXT NOT NULL,
                    path TEXT UNIQUE NOT NULL, duration REAL NOT NULL,
                    sample_rate INTEGER NOT NULL, channels INTEGER NOT NULL,
                    size INTEGER NOT NULL, waveform TEXT NOT NULL,
                    metadata TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS lyrics (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, content TEXT NOT NULL,
                    version INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS lyric_versions (
                    lyric_id TEXT NOT NULL REFERENCES lyrics(id) ON DELETE CASCADE,
                    version INTEGER NOT NULL, name TEXT NOT NULL, content TEXT NOT NULL,
                    created_at TEXT NOT NULL, PRIMARY KEY (lyric_id, version)
                );
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, state TEXT NOT NULL,
                    stage TEXT NOT NULL, progress INTEGER NOT NULL DEFAULT 0,
                    params TEXT NOT NULL, outputs TEXT NOT NULL DEFAULT '[]',
                    error TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
            """)

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def all(self, table: str) -> list[dict]:
        assert table in ("assets", "lyrics", "jobs")
        with self.connection() as db:
            return [
                self.decode(dict(row))
                for row in db.execute(f"SELECT * FROM {table} ORDER BY created_at DESC")
            ]

    def get(self, table: str, item_id: str) -> dict | None:
        assert table in ("assets", "lyrics", "jobs")
        with self.connection() as db:
            row = db.execute(f"SELECT * FROM {table} WHERE id=?", (item_id,)).fetchone()
        return self.decode(dict(row)) if row else None

    def insert(self, table: str, values: dict):
        assert table in ("assets", "lyrics", "jobs")
        keys = list(values)
        with self.connection() as db:
            db.execute(
                f"INSERT INTO {table} ({','.join(keys)}) VALUES ({','.join('?' for _ in keys)})",
                [self.encode(values[key]) for key in keys],
            )

    def update_job(self, job_id: str, **values):
        values["updated_at"] = now()
        allowed = {"state", "stage", "progress", "outputs", "error", "updated_at"}
        assert values.keys() <= allowed
        with self.connection() as db:
            db.execute(
                f"UPDATE jobs SET {','.join(f'{key}=?' for key in values)} WHERE id=?",
                [self.encode(value) for value in values.values()] + [job_id],
            )

    @staticmethod
    def encode(value):
        return json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else value

    @staticmethod
    def decode(row: dict) -> dict:
        for key in ("waveform", "metadata", "params", "outputs"):
            if key in row:
                row[key] = json.loads(row[key])
        return row
