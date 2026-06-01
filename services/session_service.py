"""Session service — patient/session/dataset CRUD backed by SQLite.

pycdms is used only when mounting a dataset folder: scan_folder() classifies
the files so we can record the dominant content_type (ct_series, rtplan, etc.).
Patient and session identity live entirely in the local SQLite database.
"""
import asyncio
import uuid
from datetime import datetime, timezone
from pathlib import Path

import aiosqlite

from database import DB_PATH
from pycdms import scan_folder, group_by_fraction


# ── Patients ──────────────────────────────────────────────────────────────────

async def get_all_patients() -> list:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM patients ORDER BY created_at") as cur:
            rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def get_patient_by_id(patient_id: str) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM patients WHERE id=?", (patient_id,)
        ) as cur:
            row = await cur.fetchone()
    return dict(row) if row else None


# ── Sessions ──────────────────────────────────────────────────────────────────

async def get_sessions_for_patient(patient_id: str) -> list:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM sessions WHERE patient_id=? ORDER BY created_at",
            (patient_id,),
        ) as cur:
            rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def create_session(patient_id: str, label: str) -> dict:
    session_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO sessions (id, patient_id, label, created_at, updated_at) "
            "VALUES (?,?,?,?,?)",
            (session_id, patient_id, label, now, now),
        )
        await db.commit()
    return {
        "id": session_id,
        "patientId": patient_id,
        "label": label,
        "createdAt": now,
        "updatedAt": now,
    }


async def get_session_by_id(session_id: str) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM sessions WHERE id=?", (session_id,)
        ) as cur:
            row = await cur.fetchone()
    return dict(row) if row else None


# ── Datasets ──────────────────────────────────────────────────────────────────

async def get_datasets_for_session(session_id: str) -> list:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM datasets WHERE session_id=? ORDER BY created_at",
            (session_id,),
        ) as cur:
            rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def mount_dataset(session_id: str, patient_data_path: str) -> dict:
    """Scan a CDMS archive folder with pycdms and register it as a dataset.

    pycdms.scan_folder() walks the directory and returns a list of CdmsFile
    objects; the dominant content type is inferred from the most common
    ContentInfo.kind value across all files.
    """
    loop = asyncio.get_event_loop()
    files = await loop.run_in_executor(
        None, scan_folder, Path(patient_data_path)
    )

    # Determine dominant content type from the scan
    if files:
        kinds = [f.content.kind for f in files if f.content is not None]
        content_type = max(set(kinds), key=kinds.count) if kinds else "unknown"
    else:
        content_type = "unknown"

    dataset_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO datasets (id, session_id, path, content_type, created_at) "
            "VALUES (?,?,?,?,?)",
            (dataset_id, session_id, str(patient_data_path), content_type, now),
        )
        await db.commit()
    return {
        "id": dataset_id,
        "sessionId": session_id,
        "path": str(patient_data_path),
        "contentType": content_type,
        "fileCount": len(files),
        "createdAt": now,
    }

