import asyncio
import json
from datetime import UTC, datetime

import aiosqlite
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from database import DB_PATH
from schemas import AccumulatedDoseResult
from services.deform_service import run_dose_accumulation

router = APIRouter()


# ── Registrations ─────────────────────────────────────────────────────────────


@router.get("/adaptive/sessions/{session_id}/registrations")
async def get_registrations(session_id: str):
    """Return stored registration results for this session.

    Results are written by the 'register' job worker after the engine completes.
    This endpoint only reads — it never invokes the engine.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT result FROM jobs "
            "WHERE session_id=? AND type='register' AND status='completed' "
            "ORDER BY created_at",
            (session_id,),
        ) as cur:
            rows = await cur.fetchall()
    results = []
    for row in rows:
        if row["result"]:
            data = json.loads(row["result"])
            if isinstance(data, list):
                results.extend(data)
            elif isinstance(data, dict) and "registrations" in data:
                results.extend(data["registrations"])
            else:
                results.append(data)
    return results


# ── Contour review status ─────────────────────────────────────────────────────


class ContourStatusBody(BaseModel):
    status: str  # pending | accepted | rejected


@router.patch("/adaptive/contours/{contour_id}/status")
async def update_contour_status(contour_id: str, body: ContourStatusBody):
    allowed = {"pending", "accepted", "rejected"}
    if body.status not in allowed:
        raise HTTPException(status_code=422, detail=f"status must be one of {allowed}")
    now = datetime.now(UTC).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT id FROM contour_reviews WHERE id=?", (contour_id,)) as cur:
            row = await cur.fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail=f"Contour review {contour_id!r} not found")
        else:
            await db.execute(
                "UPDATE contour_reviews SET status=? WHERE id=?",
                (body.status, contour_id),
            )
        await db.commit()
    return {"id": contour_id, "status": body.status, "updatedAt": now}


# ── Dose accumulation ─────────────────────────────────────────────────────────


@router.get(
    "/adaptive/sessions/{session_id}/dose-accumulation", response_model=AccumulatedDoseResult
)
async def get_dose_accumulation(session_id: str):
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, run_dose_accumulation, session_id, {})
    return result
