import hashlib
import json
import uuid
from datetime import datetime, timezone

import aiosqlite
from fastapi import APIRouter, HTTPException, UploadFile, File
from pydantic import BaseModel

from database import DB_PATH
from services.dose_service import run_phantom_calc
import asyncio

router = APIRouter()


# ── Machine CRUD ──────────────────────────────────────────────────────────────

@router.get("/commissioning/machines")
async def list_machines():
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM machines") as cur:
            rows = await cur.fetchall()
    return [dict(r) for r in rows]


class CreateMachineBody(BaseModel):
    name: str
    engine: str
    params: dict


@router.post("/commissioning/machines", status_code=201)
async def create_machine(body: CreateMachineBody):
    machine_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO machines VALUES (?,?,?,?,?,?,?,?)",
            (machine_id, body.name, body.engine, "draft",
             json.dumps(body.params), None, now, now),
        )
        await db.commit()
    return {"id": machine_id, "name": body.name, "engine": body.engine,
            "status": "draft", "params": body.params, "createdAt": now}


# ── CSV / measurement file upload ─────────────────────────────────────────────

@router.post("/commissioning/upload")
async def upload_measurement(file: UploadFile = File(...)):
    contents = await file.read()
    # Return raw bytes size for now; real implementation parses IBA/PTW/CSV
    return {"filename": file.filename, "size": len(contents), "status": "received"}


# ── Water-phantom calculation ─────────────────────────────────────────────────

class PhantomCalcBody(BaseModel):
    machine_id: str
    engine: str
    parameters: dict


@router.post("/commissioning/calculate_water_phantom")
async def calculate_water_phantom(body: PhantomCalcBody):
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(
        None,
        run_phantom_calc,
        {"machineId": body.machine_id, "engine": body.engine,
         "parameters": body.parameters},
    )
    return result


# ── Machine lock ──────────────────────────────────────────────────────────────

@router.post("/commissioning/lock")
async def lock_machine(body: dict):
    machine_id = body.get("machine_id")
    if not machine_id:
        raise HTTPException(status_code=422, detail="machine_id required")
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM machines WHERE id=?", (machine_id,)) as cur:
            row = await cur.fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Machine not found")
        machine = dict(row)
    payload = json.dumps({"id": machine_id, "params": machine["params"]}, sort_keys=True)
    locked_hash = hashlib.sha256(payload.encode()).hexdigest()
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE machines SET status='locked', locked_hash=?, updated_at=? WHERE id=?",
            (locked_hash, now, machine_id),
        )
        await db.commit()
    return {"machine_id": machine_id, "locked_hash": locked_hash, "lockedAt": now}
