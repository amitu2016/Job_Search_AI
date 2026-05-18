"""SQLite-backed job tracker — dedup, status persistence, run history."""

import sqlite3
from datetime import datetime
from pathlib import Path

from portals.base import ApplyResult, ApplyStatus, Job

DB_PATH = Path(__file__).parent.parent / "data" / "jobs.db"


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with _connect() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS jobs (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                url_key     TEXT    UNIQUE NOT NULL,
                url         TEXT    NOT NULL,
                title       TEXT    NOT NULL,
                company     TEXT    NOT NULL,
                location    TEXT    NOT NULL,
                portal      TEXT    NOT NULL,
                score       INTEGER DEFAULT 0,
                score_reason TEXT   DEFAULT '',
                status      TEXT    DEFAULT 'seen',
                error       TEXT    DEFAULT '',
                salary      TEXT    DEFAULT '',
                posted_at   TEXT    DEFAULT '',
                seen_at     TEXT    NOT NULL,
                applied_at  TEXT
            );

            CREATE TABLE IF NOT EXISTS runs (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                started_at  TEXT    NOT NULL,
                finished_at TEXT,
                jobs_found  INTEGER DEFAULT 0,
                jobs_scored INTEGER DEFAULT 0,
                jobs_applied INTEGER DEFAULT 0,
                jobs_failed INTEGER DEFAULT 0
            );
        """)


def is_seen(job: Job) -> bool:
    with _connect() as conn:
        row = conn.execute(
            "SELECT id FROM jobs WHERE url_key = ?", (job.key,)
        ).fetchone()
        return row is not None


def mark_seen(job: Job) -> None:
    with _connect() as conn:
        conn.execute(
            """INSERT OR IGNORE INTO jobs
               (url_key, url, title, company, location, portal, score, score_reason, salary, posted_at, seen_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (
                job.key, job.url, job.title, job.company, job.location,
                job.portal, job.score, job.score_reason,
                job.salary, job.posted_at,
                datetime.utcnow().isoformat(),
            ),
        )


def mark_result(result: ApplyResult) -> None:
    with _connect() as conn:
        conn.execute(
            """UPDATE jobs SET status = ?, error = ?, applied_at = ?,
               score = ?, score_reason = ?
               WHERE url_key = ?""",
            (
                result.status.value,
                result.error,
                result.applied_at.isoformat() if result.status == ApplyStatus.APPLIED else None,
                result.job.score,
                result.job.score_reason,
                result.job.key,
            ),
        )


def start_run() -> int:
    with _connect() as conn:
        cur = conn.execute(
            "INSERT INTO runs (started_at) VALUES (?)",
            (datetime.utcnow().isoformat(),),
        )
        return cur.lastrowid


def finish_run(run_id: int, found: int, scored: int, applied: int, failed: int) -> None:
    with _connect() as conn:
        conn.execute(
            """UPDATE runs SET finished_at=?, jobs_found=?, jobs_scored=?, jobs_applied=?, jobs_failed=?
               WHERE id=?""",
            (datetime.utcnow().isoformat(), found, scored, applied, failed, run_id),
        )


def recent_applied(limit: int = 20) -> list[sqlite3.Row]:
    with _connect() as conn:
        return conn.execute(
            "SELECT title, company, location, portal, score, applied_at FROM jobs "
            "WHERE status = 'applied' ORDER BY applied_at DESC LIMIT ?",
            (limit,),
        ).fetchall()


def run_summary(run_id: int) -> sqlite3.Row | None:
    with _connect() as conn:
        return conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()


def get_stats() -> dict:
    with _connect() as conn:
        total_applied = conn.execute(
            "SELECT count(*) FROM jobs WHERE status='applied'"
        ).fetchone()[0]
        total_found = conn.execute("SELECT count(*) FROM jobs").fetchone()[0]
        last_run = conn.execute(
            "SELECT started_at, jobs_applied, jobs_found FROM runs ORDER BY id DESC LIMIT 1"
        ).fetchone()
        runs_total = conn.execute("SELECT count(*) FROM runs").fetchone()[0]
    return {
        "total_applied": total_applied,
        "total_found": total_found,
        "runs_total": runs_total,
        "last_run_at": last_run["started_at"] if last_run else None,
        "last_run_applied": last_run["jobs_applied"] if last_run else 0,
    }


def get_recent_runs(limit: int = 20) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(r) for r in rows]


def get_jobs(status: str | None = None, limit: int = 100) -> list[dict]:
    with _connect() as conn:
        if status and status != "all":
            rows = conn.execute(
                "SELECT * FROM jobs WHERE status=? ORDER BY id DESC LIMIT ?",
                (status, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM jobs ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
    return [dict(r) for r in rows]
