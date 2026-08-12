import asyncio
import contextlib
import json
import mimetypes
import uuid
from datetime import UTC, datetime
from pathlib import Path

import aiosqlite

from database import DB_PATH
from jobs.handlers import HANDLERS

_worker_task: asyncio.Task | None = None
_worker_db_path: str | None = None
_stop_worker = asyncio.Event()
_poll_interval_s = 0.05


def _now() -> str:
    return datetime.now(UTC).isoformat()


async def start_worker() -> None:
    """Start the SQLite worker and make interrupted jobs eligible for retry."""
    global _worker_db_path, _worker_task
    if _worker_task is not None and not _worker_task.done():
        if _worker_db_path == DB_PATH:
            return
        await stop_worker()
    _stop_worker.clear()
    _worker_db_path = DB_PATH
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE jobs SET status='queued', message='Recovered after restart', "
            "started_at=NULL, updated_at=? WHERE status='running'",
            (_now(),),
        )
        await db.commit()
    _worker_task = asyncio.create_task(_worker_loop(), name="axioma-sidecar-job-worker")


async def stop_worker() -> None:
    """Stop the worker cleanly during application shutdown."""
    global _worker_db_path, _worker_task
    if _worker_task is None:
        return
    _stop_worker.set()
    with contextlib.suppress(asyncio.CancelledError):
        await _worker_task
    _worker_task = None
    _worker_db_path = None


async def _worker_loop() -> None:
    while not _stop_worker.is_set():
        claimed = await _claim_next_job()
        if claimed is None:
            await asyncio.sleep(_poll_interval_s)
            continue
        job_id, session_id, job_type, params = claimed
        await _run_job(job_id, session_id, job_type, params)


async def _claim_next_job() -> tuple[str, str, str, dict] | None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("BEGIN IMMEDIATE")
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT id, session_id, type, params FROM jobs "
            "WHERE status='queued' AND cancel_requested=0 ORDER BY created_at LIMIT 1"
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            await db.commit()
            return None
        now = _now()
        await db.execute(
            "UPDATE jobs SET status='running', started_at=?, updated_at=? WHERE id=?",
            (now, now, row["id"]),
        )
        await db.commit()
    return row["id"], row["session_id"], row["type"], json.loads(row["params"] or "{}")


async def enqueue_job(session_id: str, job_type: str, params: dict) -> dict:
    await start_worker()
    job_id = str(uuid.uuid4())
    now = _now()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO jobs "
            "(id, session_id, type, status, progress, message, params, result, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (job_id, session_id, job_type, "queued", 0.0, None, json.dumps(params), None, now, now),
        )
        await db.commit()
    return await get_job(job_id)


async def _run_job(job_id: str, session_id: str, job_type: str, params: dict):
    try:
        handler = HANDLERS.get(job_type)
        if handler is None:
            raise ValueError(f"Unknown job type: {job_type!r}")
        result = await handler(
            session_id,
            params,
            progress_cb=lambda p: _set_progress(job_id, p),
        )
        if await is_cancel_requested(job_id):
            await _set_status(job_id, "cancelled", message="Job cancelled")
            return
        await _register_result_artifacts(job_id, result)
        await _set_status(job_id, "completed", result=result)
    except Exception as exc:
        await _set_status(job_id, "failed", message=str(exc))


async def _set_status(
    job_id: str,
    status: str,
    message: str | None = None,
    result: dict | None = None,
):
    now = _now()
    finished_at = now if status in {"completed", "failed", "cancelled"} else None
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE jobs SET status=?, message=?, result=?, finished_at=COALESCE(?, finished_at), "
            "updated_at=? WHERE id=?",
            (
                status,
                message,
                json.dumps(result) if result is not None else None,
                finished_at,
                now,
                job_id,
            ),
        )
        await db.commit()


async def _set_progress(job_id: str, progress: float):
    now = _now()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE jobs SET progress=?, updated_at=? WHERE id=?",
            (progress, now, job_id),
        )
        await db.commit()


async def is_cancel_requested(job_id: str) -> bool:
    async with (
        aiosqlite.connect(DB_PATH) as db,
        db.execute("SELECT cancel_requested FROM jobs WHERE id=?", (job_id,)) as cur,
    ):
        row = await cur.fetchone()
    return bool(row and row[0])


async def cancel_job(job_id: str) -> dict | None:
    now = _now()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE jobs SET cancel_requested=1, status=CASE WHEN status='queued' "
            "THEN 'cancelled' ELSE status END, message=CASE WHEN status='queued' "
            "THEN 'Job cancelled' ELSE message END, finished_at=CASE WHEN status='queued' "
            "THEN ? ELSE finished_at END, updated_at=? WHERE id=? AND status IN ('queued','running')",
            (now, now, job_id),
        )
        await db.commit()
    return await get_job(job_id)


async def get_job(job_id: str) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)) as cur:
            row = await cur.fetchone()
    return _decode_job_row(dict(row)) if row else None


async def get_jobs_for_session(session_id: str) -> list:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM jobs WHERE session_id=? ORDER BY created_at DESC",
            (session_id,),
        ) as cur:
            rows = await cur.fetchall()
    return [_decode_job_row(dict(r)) for r in rows]


async def get_job_artifacts(job_id: str) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT id, job_id, name, path, media_type, size_bytes, created_at "
            "FROM job_artifacts WHERE job_id=? ORDER BY created_at",
            (job_id,),
        ) as cur:
            rows = await cur.fetchall()
    return [dict(row) for row in rows]


async def _register_result_artifacts(job_id: str, result: object) -> None:
    if not isinstance(result, dict):
        return
    candidates: list[Path] = []
    output_dir = result.get("out_dir")
    if isinstance(output_dir, str):
        directory = Path(output_dir)
        if directory.is_dir():
            candidates.extend(path for path in directory.rglob("*") if path.is_file())
    explicit = result.get("artifacts", [])
    if isinstance(explicit, list):
        candidates.extend(Path(path) for path in explicit if isinstance(path, str))

    unique_paths = []
    seen = set()
    for path in candidates:
        try:
            resolved = path.resolve()
        except OSError:
            continue
        if not resolved.is_file() or resolved in seen:
            continue
        seen.add(resolved)
        unique_paths.append(resolved)

    now = _now()
    async with aiosqlite.connect(DB_PATH) as db:
        for path in unique_paths:
            media_type = mimetypes.guess_type(path.name)[0]
            await db.execute(
                "INSERT INTO job_artifacts "
                "(id, job_id, name, path, media_type, size_bytes, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    str(uuid.uuid4()),
                    job_id,
                    path.name,
                    str(path),
                    media_type,
                    path.stat().st_size,
                    now,
                ),
            )
        await db.commit()


def _decode_job_row(row: dict) -> dict:
    """Convert SQLite JSON columns into the public API representation."""
    for field in ("params", "result"):
        value = row.get(field)
        if value is not None:
            row[field] = json.loads(value)
    return row
