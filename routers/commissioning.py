import asyncio
import csv
import hashlib
import io
import json
import re
import uuid
from datetime import UTC, datetime

import aiosqlite
from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel

from database import DB_PATH
from schemas import BeamModel, DoseResult, GoldenData, MachineRecord
from services.dose_service import run_phantom_calc

router = APIRouter()


# Columns whose canonical unit is centimeters; mm variants are scaled on read.
_MM_TO_CM_COLUMNS = {"depthmm", "positionmm", "fieldsizemm"}


def _canonical_measurement_column(name: str) -> str:
    compact = re.sub(r"[^a-z0-9]", "", name.lower())
    aliases = {
        "depth": "depth",
        "depthcm": "depth",
        "depthmm": "depth",
        "dose": "dose",
        "dosepercent": "dose",
        "relativedose": "dose",
        "position": "position",
        "positioncm": "position",
        "positionmm": "position",
        "lateralposition": "position",
        "scanposition": "position",
        "fieldsize": "fieldsize",
        "fieldsizecm": "fieldsize",
        "fieldsizemm": "fieldsize",
        "sf": "sf",
        "outputfactor": "sf",
        "relativeoutput": "sf",
    }
    return aliases.get(compact, compact)


def _column_unit_scale(name: str) -> float:
    """Return the cm-conversion factor for a raw (pre-canonicalization) column name."""
    compact = re.sub(r"[^a-z0-9]", "", name.lower())
    return 0.1 if compact in _MM_TO_CM_COLUMNS else 1.0


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


class BeamModelBody(BaseModel):
    version: str
    parameters: dict


@router.get("/commissioning/machines/{machine_id}/beam-model", response_model=BeamModel)
async def get_beam_model(machine_id: str):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM machines WHERE id=?", (machine_id,)) as cur:
            row = await cur.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Machine not found")
    params = json.loads(row["params"] or "{}")
    model = params.get("beam_model", {})
    payload = json.dumps(model.get("parameters", {}), sort_keys=True)
    return {
        "machine_id": machine_id,
        "version": model.get("version", "draft"),
        "sha256": hashlib.sha256(payload.encode()).hexdigest(),
        "parameters": model.get("parameters", {}),
    }


@router.put("/commissioning/machines/{machine_id}/beam-model", response_model=BeamModel)
async def put_beam_model(machine_id: str, body: BeamModelBody):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM machines WHERE id=?", (machine_id,)) as cur:
            row = await cur.fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Machine not found")
        if row["status"] == "locked":
            raise HTTPException(status_code=409, detail="Locked machine beam model is immutable")
        params = json.loads(row["params"] or "{}")
        params["beam_model"] = {"version": body.version, "parameters": body.parameters}
        now = datetime.now(UTC).isoformat()
        await db.execute(
            "UPDATE machines SET params=?, updated_at=? WHERE id=?",
            (json.dumps(params, sort_keys=True), now, machine_id),
        )
        await db.commit()
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(
        None,
        _sync_to_gendosecalc,
        machine_id,
        {"id": machine_id, "beam_model": params["beam_model"]},
    )
    payload = json.dumps(body.parameters, sort_keys=True)
    return {
        "machine_id": machine_id,
        "version": body.version,
        "sha256": hashlib.sha256(payload.encode()).hexdigest(),
        "parameters": body.parameters,
    }


@router.get("/commissioning/machines/{machine_id}/golden-data", response_model=GoldenData)
async def get_golden_data(machine_id: str):
    async with (
        aiosqlite.connect(DB_PATH) as db,
        db.execute("SELECT params FROM machines WHERE id=?", (machine_id,)) as cur,
    ):
        row = await cur.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Machine not found")
    params = json.loads(row[0] or "{}")
    golden = params.get("golden_data", {})
    data = golden.get("data", {})
    payload = json.dumps(data, sort_keys=True)
    return {
        "machine_id": machine_id,
        "version": golden.get("version"),
        "sha256": golden.get("sha256") or hashlib.sha256(payload.encode()).hexdigest(),
        "data": data,
    }


# ── CSV / measurement file upload ─────────────────────────────────────────────


@router.post("/commissioning/upload")
async def upload_measurement(file: UploadFile = File()):  # noqa: B008
    contents = await file.read()
    if len(contents) > 10 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Measurement file exceeds 10 MiB limit")
    if not (file.filename or "").lower().endswith(".csv"):
        raise HTTPException(status_code=415, detail="Only CSV measurement files are supported")
    try:
        text = contents.decode("utf-8-sig")
        try:
            dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t")
        except csv.Error:
            dialect = csv.excel
        reader = csv.DictReader(io.StringIO(text), dialect=dialect)
        if not reader.fieldnames:
            raise ValueError("CSV header is missing")
        rows = []
        for row in reader:
            normalized_row = {}
            for key, value in row.items():
                name = (key or "").strip()
                raw_value = (value or "").strip()
                try:
                    numeric_value = raw_value.replace(",", ".") if "," in raw_value else raw_value
                    normalized_row[name] = float(numeric_value)
                except ValueError:
                    normalized_row[name] = raw_value
            rows.append(normalized_row)
    except (UnicodeDecodeError, csv.Error, ValueError) as exc:
        raise HTTPException(status_code=422, detail=f"Invalid measurement CSV: {exc}") from exc

    columns = [column.strip() for column in reader.fieldnames]
    column_aliases = {_canonical_measurement_column(column): column for column in columns}
    canonical_columns = set(column_aliases)

    def _x_value(row: dict, column: str) -> float | str:
        value = row[column]
        if isinstance(value, float):
            return value * _column_unit_scale(column)
        return value

    normalized: dict[str, list[dict]] = {}
    if {"depth", "dose"}.issubset(canonical_columns):
        depth_column = column_aliases["depth"]
        dose_column = column_aliases["dose"]
        normalized["pdd"] = [
            {"x": _x_value(row, depth_column), "y": row[dose_column]} for row in rows
        ]
    elif {"position", "dose"}.issubset(canonical_columns):
        position_column = column_aliases["position"]
        dose_column = column_aliases["dose"]
        category = "profile10cm" if "10cm" in (file.filename or "").lower() else "profileDmax"
        normalized[category] = [
            {"x": _x_value(row, position_column), "y": row[dose_column]} for row in rows
        ]
    elif {"fieldsize", "sf"}.issubset(canonical_columns):
        field_column = column_aliases["fieldsize"]
        sf_column = column_aliases["sf"]
        normalized["outputFactors"] = [
            {"fieldSize": _x_value(row, field_column), "sf": row[sf_column]} for row in rows
        ]

    return {
        "filename": file.filename,
        "size": len(contents),
        "status": "parsed",
        "sha256": hashlib.sha256(contents).hexdigest(),
        "columns": columns,
        "row_count": len(rows),
        "measurements": rows,
        "normalized": normalized,
    }


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
