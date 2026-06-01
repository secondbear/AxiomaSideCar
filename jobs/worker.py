import asyncio
import json
import uuid
from datetime import datetime, timezone

import aiosqlite

from database import DB_PATH
from jobs.handlers import HANDLERS


async def enqueue_job(session_id: str, job_type: str, params: dict) -> dict:
    job_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO jobs VALUES (?,?,?,?,?,?,?,?,?,?)",
            (job_id, session_id, job_type, "queued", 0.0,
             None, json.dumps(params), None, now, now),
        )
        await db.commit()
    asyncio.create_task(_run_job(job_id, session_id, job_type, params))
    return await get_job(job_id)


async def _run_job(job_id: str, session_id: str, job_type: str, params: dict):
    await _set_status(job_id, "running")
    try:
        handler = HANDLERS.get(job_type)
        if handler is None:
            raise ValueError(f"Unknown job type: {job_type!r}")
        result = await handler(
            session_id, params, progress_cb=lambda p: _set_progress(job_id, p)
        )
        await _set_status(job_id, "completed", result=result)
    except Exception as exc:
        await _set_status(job_id, "failed", message=str(exc))


async def _set_status(
    job_id: str,
    status: str,
    message: str | None = None,
    result: dict | None = None,
):
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE jobs SET status=?, message=?, result=?, updated_at=? WHERE id=?",
            (status, message, json.dumps(result) if result is not None else None, now, job_id),
        )
        await db.commit()


async def _set_progress(job_id: str, progress: float):
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE jobs SET progress=?, updated_at=? WHERE id=?",
            (progress, now, job_id),
        )
        await db.commit()


async def get_job(job_id: str) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)) as cur:
            row = await cur.fetchone()
    return dict(row) if row else None


async def get_jobs_for_session(session_id: str) -> list:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM jobs WHERE session_id=? ORDER BY created_at DESC",
            (session_id,),
        ) as cur:
            rows = await cur.fetchall()
    return [dict(r) for r in rows]
