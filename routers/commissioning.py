import asyncio
import hashlib
import json
import uuid
from datetime import UTC, datetime

import aiosqlite
from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel

from database import DB_PATH
from schemas import DoseResult, MachineRecord
from services.dose_service import run_phantom_calc

router = APIRouter()


def _sync_to_gendosecalc(machine_id: str, machine_dict: dict) -> None:
    """Write-through: keep GenDoseCalc's machines.yaml in sync with SQLite.

    GenDoseCalc's ClinicalRunContext.build() auto-selects beam models from
    data/Commissioning/machines.yaml via gendosecalc.service.machines.  Any
    machine that exists only in SQLite will be invisible to the dose engine.
    This function ensures both stores stay consistent on every write.

    Called synchronously from the router (no heavy I/O — just YAML read/write).
    """
    from gendosecalc.service.machines import add_machine, update_machine

    try:
        update_machine(machine_id, machine_dict)
    except KeyError:
        add_machine(machine_dict)


# ── Machine CRUD ──────────────────────────────────────────────────────────────


@router.get("/commissioning/machines", response_model=list[MachineRecord])
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


@router.post("/commissioning/machines", status_code=201, response_model=MachineRecord)
async def create_machine(body: CreateMachineBody):
    machine_id = str(uuid.uuid4())
    now = datetime.now(UTC).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO machines VALUES (?,?,?,?,?,?,?,?)",
            (machine_id, body.name, body.engine, "draft", json.dumps(body.params), None, now, now),
        )
        await db.commit()
    # Keep GenDoseCalc YAML registry in sync so the dose engine can find the machine
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(
        None,
        _sync_to_gendosecalc,
        machine_id,
        {
            "id": machine_id,
            "name": body.name,
            "engine": body.engine,
            "status": "draft",
            **body.params,
        },
    )
    return {
        "id": machine_id,
        "name": body.name,
        "engine": body.engine,
        "status": "draft",
        "params": body.params,
        "created_at": now,
    }


# ── CSV / measurement file upload ─────────────────────────────────────────────


@router.post("/commissioning/upload")
async def upload_measurement(file: UploadFile = File()):  # noqa: B008
    contents = await file.read()
    # Return raw bytes size for now; real implementation parses IBA/PTW/CSV
    return {"filename": file.filename, "size": len(contents), "status": "received"}


# ── Water-phantom calculation ─────────────────────────────────────────────────


class PhantomCalcBody(BaseModel):
    machine_id: str
    engine: str
    parameters: dict


@router.post("/commissioning/calculate_water_phantom", response_model=DoseResult)
async def calculate_water_phantom(body: PhantomCalcBody):
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(
        None,
        run_phantom_calc,
        {"machineId": body.machine_id, "engine": body.engine, "parameters": body.parameters},
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
    now = datetime.now(UTC).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE machines SET status='locked', locked_hash=?, updated_at=? WHERE id=?",
            (locked_hash, now, machine_id),
        )
        await db.commit()
    # Propagate locked status to GenDoseCalc YAML registry
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(
        None,
        _sync_to_gendosecalc,
        machine_id,
        {"id": machine_id, "status": "locked", "locked_hash": locked_hash},
    )
    return {"machine_id": machine_id, "locked_hash": locked_hash, "lockedAt": now}
