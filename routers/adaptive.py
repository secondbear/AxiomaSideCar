import uuid
from datetime import datetime, timezone

import aiosqlite
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from database import DB_PATH
from services.deform_service import run_registration, run_dose_accumulation
import asyncio

router = APIRouter()


# ── Registrations ─────────────────────────────────────────────────────────────

@router.get("/adaptive/sessions/{session_id}/registrations")
async def get_registrations(session_id: str):
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(
        None, run_registration, session_id, {}
    )
    return result.get("registrations", [])


# ── Contour review status ─────────────────────────────────────────────────────

class ContourStatusBody(BaseModel):
    status: str  # pending | accepted | rejected


@router.patch("/adaptive/contours/{contour_id}/status")
async def update_contour_status(contour_id: str, body: ContourStatusBody):
    allowed = {"pending", "accepted", "rejected"}
    if body.status not in allowed:
        raise HTTPException(status_code=422, detail=f"status must be one of {allowed}")
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT id FROM contour_reviews WHERE id=?", (contour_id,)
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            # Auto-create the review record if it doesn't exist yet
            await db.execute(
                "INSERT INTO contour_reviews (id, session_id, fraction_index, structure_id, status) "
                "VALUES (?, '', 0, '', ?)",
                (contour_id, body.status),
            )
        else:
            await db.execute(
                "UPDATE contour_reviews SET status=? WHERE id=?",
                (body.status, contour_id),
            )
        await db.commit()
    return {"id": contour_id, "status": body.status, "updatedAt": now}


# ── Dose accumulation ─────────────────────────────────────────────────────────

@router.get("/adaptive/sessions/{session_id}/dose-accumulation")
async def get_dose_accumulation(session_id: str):
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(
        None, run_dose_accumulation, session_id, {}
    )
    return result
