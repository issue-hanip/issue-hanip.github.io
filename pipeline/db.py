import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS candidates (
    id TEXT PRIMARY KEY,              -- 대표 기사 링크의 해시
    feed TEXT NOT NULL,
    title TEXT NOT NULL,              -- 대표 기사 제목 (언론사명 제외)
    outlet TEXT,
    link TEXT NOT NULL,
    related TEXT NOT NULL DEFAULT '[]',  -- [{"title","outlet"}] 같은 이슈의 다른 기사
    heat INTEGER NOT NULL DEFAULT 1,  -- 같은 이슈를 다룬 언론사 수 (RSS는 최대 5)
    rank INTEGER NOT NULL DEFAULT 0,  -- 피드 안 노출 순서 (0이 맨 위)
    published_at TEXT NOT NULL,
    seen_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'new' -- new | picked | skipped | used
);

CREATE TABLE IF NOT EXISTS stories (
    slug TEXT PRIMARY KEY,
    candidate_id TEXT,
    category TEXT NOT NULL,           -- 연예 | 사회 | 화제 | 트렌드 | 꿀팁 | 스포츠 | 경제 | 기타
    title TEXT NOT NULL,
    description TEXT NOT NULL,
    body_md TEXT NOT NULL,
    tags TEXT NOT NULL DEFAULT '[]',
    sources TEXT NOT NULL DEFAULT '[]',  -- [{"outlet","url","title"}]
    shorts TEXT NOT NULL DEFAULT '{}',   -- 숏츠 대본 JSON
    review TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'published'  -- published | withdrawn
);

CREATE TABLE IF NOT EXISTS rejections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    slug TEXT NOT NULL,
    reasons TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS videos (
    slug TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    description TEXT NOT NULL,
    tags TEXT NOT NULL,
    seconds REAL NOT NULL,
    status TEXT NOT NULL,             -- rendered | uploaded | upload_failed
    youtube_id TEXT,
    error TEXT,
    variant TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS social_posts (
    slug TEXT NOT NULL,
    platform TEXT NOT NULL,
    status TEXT NOT NULL,             -- posted | failed
    remote_id TEXT,
    error TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (slug, platform)
);

CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT,
    summary TEXT
);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(path: Path | str = DB_PATH) -> sqlite3.Connection:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def log_rejection(conn, slug: str, reasons: list[str]) -> None:
    conn.execute("INSERT INTO rejections (slug, reasons, created_at) VALUES (?, ?, ?)",
                 (slug, json.dumps(reasons, ensure_ascii=False), now_iso()))
    conn.commit()


def start_run(conn) -> int:
    cur = conn.execute("INSERT INTO runs (started_at, status) VALUES (?, 'running')", (now_iso(),))
    conn.commit()
    return cur.lastrowid


def finish_run(conn, run_id: int, status: str, summary: dict) -> None:
    conn.execute("UPDATE runs SET finished_at=?, status=?, summary=? WHERE id=?",
                 (now_iso(), status, json.dumps(summary, ensure_ascii=False), run_id))
    conn.commit()
