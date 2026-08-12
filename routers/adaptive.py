import json
from datetime import UTC, datetime

import aiosqlite
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from database import DB_PATH
from schemas import (
    AccumulatedDoseResult,
    ContourReview,
    Fraction,
    RegistrationMetric,
    RegistrationSummary,
)

router = APIRouter()


def _registration_metrics(value: object) -> list[dict]:
    if isinstance(value, list):
        metrics = []
        for item in value:
            metrics.extend(_registration_metrics(item))
        return metrics
    if not isinstance(value, dict):
        return []
    for key in ("registrations", "metrics", "results"):
        if key in value:
            return _registration_metrics(value[key])
    required = {"fractionIndex", "rmsSurfaceDistanceMm", "meanDice"}
    if required.issubset(value):
        return [
            {
                "fractionIndex": int(value["fractionIndex"]),
                "rmsSurfaceDistanceMm": float(value["rmsSurfaceDistanceMm"]),
                "meanDice": float(value["meanDice"]),
                "approved": bool(value.get("approved", False)),
            }
        ]
    return []


# ── Registrations ─────────────────────────────────────────────────────────────


@router.get("/sessions/{session_id}/fractions", response_model=list[Fraction])
async def get_fractions(session_id: str):
    """Return ordered fraction groups already discovered in this session."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT di.id, di.fraction, di.machine FROM dataset_items di "
            "JOIN datasets d ON d.id=di.dataset_id WHERE d.session_id=? "
            "AND di.kind='cdms_group' ORDER BY di.added_at",
            (session_id,),
        ) as cur:
            rows = await cur.fetchall()
    fractions = []
    for index, row in enumerate(rows):
        label = row["fraction"] or f"Fraction {index + 1}"
        fractions.append(
            {
                "index": index,
                "label": label,
                "session_id": session_id,
                "dataset_item_id": row["id"],
                "machine": row["machine"],
            }
        )
    return fractions


@router.get(
    "/adaptive/sessions/{session_id}/registrations", response_model=list[RegistrationMetric]
)
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
            results.extend(_registration_metrics(json.loads(row["result"])))
    return results


@router.get("/registrations/{registration_id}", response_model=RegistrationSummary)
async def get_registration(registration_id: str):
    """Return one completed registration job without invoking the engine."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT id, session_id, status, result FROM jobs WHERE id=? AND type='register'",
            (registration_id,),
        ) as cur:
            row = await cur.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Registration not found")
    result = json.loads(row["result"]) if row["result"] else None
    return {
        "id": row["id"],
        "session_id": row["session_id"],
        "job_id": row["id"],
        "status": row["status"],
        "result": result,
        "metrics": _registration_metrics(result),
    }


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


@router.post("/registrations/{registration_id}/contours/{roi}/accept", response_model=ContourReview)
async def accept_contour(registration_id: str, roi: str):
    """Persist acceptance for a registration ROI; repeated acceptance is idempotent."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT session_id FROM jobs WHERE id=? AND type='register'",
            (registration_id,),
        ) as cur:
            registration = await cur.fetchone()
        if registration is None:
            raise HTTPException(status_code=404, detail="Registration not found")
        review_id = f"{registration_id}:{roi}"
        await db.execute(
            "INSERT INTO contour_reviews (id, session_id, fraction_index, structure_id, status) "
            "VALUES (?, ?, ?, ?, 'accepted') ON CONFLICT(id) DO UPDATE SET status='accepted'",
            (review_id, registration["session_id"], 0, roi),
        )
        await db.commit()
        async with db.execute(
            "SELECT id, session_id, fraction_index, structure_id, status "
            "FROM contour_reviews WHERE id=?",
            (review_id,),
        ) as cur:
            review = await cur.fetchone()
    return dict(review)


# ── Dose accumulation ─────────────────────────────────────────────────────────


@router.get(
    "/adaptive/sessions/{session_id}/dose-accumulation", response_model=AccumulatedDoseResult
)
async def get_dose_accumulation(session_id: str):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT result FROM jobs WHERE session_id=? AND type='dose-accumulation' "
            "AND status='completed' ORDER BY created_at DESC LIMIT 1",
            (session_id,),
        ) as cur:
            row = await cur.fetchone()
    if row is None or row["result"] is None:
        raise HTTPException(status_code=404, detail="No completed dose accumulation found")
    return json.loads(row["result"])
