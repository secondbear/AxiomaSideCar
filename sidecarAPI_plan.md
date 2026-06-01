# Axioma Sidecar — Implementation Plan

## Project context (for other agents)

This document describes the **`axioma-sidecar`** repository — a FastAPI orchestrator
that bridges the Axioma Studio desktop frontend with three existing Python backends.

### The full system at a glance

```
AxiomaUX  (this repo — Tauri + React, port 1420)
    │  HTTP  localhost:8000
    ▼
axioma-sidecar  (new repo — FastAPI orchestrator)
    ├── imports DeformCT      as a Python library  (-e ../DeformCT)
    ├── imports GenDoseCalc   as a Python library  (-e ../GenDoseCalc)
    └── imports pycdms        as a Python library  (-e ../pycdms)
```

The frontend (`AxiomaUX`) never calls DeformCT or GenDoseCalc directly. Every
backend call flows through `src/gateway/LocalDataGateway.ts` → `localhost:8000`.
The sidecar owns the job queue, session state, and all orchestration logic.

### Existing backend tools

| Repo | Language | Role |
|------|----------|------|
| `DeformCT` | Python | Deformable image registration — produces DVF (deformation vector field) per fraction, RMS surface distance, mean Dice |
| `GenDoseCalc` | Python | Monte-Carlo / pencil-beam dose engine — produces 3D dose volumes, water-phantom PDD/profiles |
| `pycdms` | Python module | DICOM/NIfTI catalogue — patient index, DICOM parsing, CT/RTStruct/RTDOSE loading, slice extraction |

### Frontend routes that need live data

| Route | Key backend calls |
|-------|------------------|
| `/library` | patients, sessions, datasets, mount |
| `/planning` | slice streaming (hot path — every scroll) |
| `/analysis` | DVH job, gamma job |
| `/commissioning` | machine CRUD, phantom calc, lock |
| `/adaptive` | register job, contour PATCH, dose-accumulation job |

---

## Repository structure

```
axioma-sidecar/
├── main.py                  ← FastAPI app factory, CORS, startup/shutdown
├── database.py              ← SQLite setup (aiosqlite), table creation
├── requirements.txt         ← editable installs of the three engine repos
├── pyproject.toml
│
├── routers/
│   ├── patients.py          ← GET /api/v1/patients, /patients/:id
│   ├── sessions.py          ← GET/POST /api/v1/sessions, /sessions/:id
│   ├── datasets.py          ← GET /api/v1/sessions/:id/datasets
│   │                           POST /api/v1/sessions/:id/datasets/mount
│   ├── slices.py            ← GET /api/v1/datasets/:id/slice  (binary hot path)
│   ├── jobs.py              ← GET/POST /api/v1/sessions/:id/jobs
│   │                           GET /api/v1/jobs/:id
│   ├── commissioning.py     ← GET /api/v1/commissioning/machines
│   │                           POST /api/v1/commissioning/upload
│   │                           POST /api/v1/commissioning/calculate_water_phantom
│   │                           POST /api/v1/commissioning/lock
│   └── adaptive.py          ← GET  /api/v1/adaptive/sessions/:id/registrations
│                               PATCH /api/v1/adaptive/contours/:id/status
│                               GET  /api/v1/adaptive/sessions/:id/dose-accumulation
│
├── services/
│   ├── session_service.py   ← wraps pycdms patient/session catalogue
│   ├── slice_service.py     ← wraps pycdms slice extraction → bytes
│   ├── dose_service.py      ← wraps GenDoseCalc core API
│   └── deform_service.py    ← wraps DeformCT core API
│
└── jobs/
    ├── worker.py            ← asyncio task runner, updates job rows in SQLite
    └── handlers.py          ← one async function per job type
```

---

## requirements.txt

```
# FastAPI stack
fastapi>=0.115
uvicorn[standard]>=0.32
aiosqlite>=0.20
python-multipart>=0.0.12    # multipart file uploads (commissioning)
httpx>=0.28                 # optional: health-check probes

# Editable installs — all three engine repos cloned as siblings
-e ../DeformCT
-e ../GenDoseCalc
-e ../pycdms

# Shared scientific stack (pin once engines agree on versions)
numpy>=1.26
scipy>=1.13
pydicom>=2.4
```

> **Note:** The `-e` flag means Python resolves the import straight from the
> sibling repo on disk. You can edit `DeformCT/deformct/core.py` and the sidecar
> picks up the change immediately on the next request — no reinstall needed.

---

## main.py skeleton

```python
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from database import init_db
from routers import patients, sessions, datasets, slices, jobs, commissioning, adaptive


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield


app = FastAPI(title="Axioma Sidecar", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:1420"],  # AxiomaUX dev server
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(patients.router,      prefix="/api/v1")
app.include_router(sessions.router,      prefix="/api/v1")
app.include_router(datasets.router,      prefix="/api/v1")
app.include_router(slices.router,        prefix="/api/v1")
app.include_router(jobs.router,          prefix="/api/v1")
app.include_router(commissioning.router, prefix="/api/v1")
app.include_router(adaptive.router,      prefix="/api/v1")
```

---

## database.py — SQLite schema

```python
import aiosqlite

DB_PATH = "axioma.db"

CREATE_TABLES = """
CREATE TABLE IF NOT EXISTS jobs (
    id          TEXT PRIMARY KEY,
    session_id  TEXT NOT NULL,
    type        TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'queued',
    progress    REAL NOT NULL DEFAULT 0.0,
    message     TEXT,
    params      TEXT,           -- JSON blob
    result      TEXT,           -- JSON blob set on completion
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS machines (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    engine      TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'draft',
    params      TEXT NOT NULL,  -- JSON: {mu, sigmaP, sigmaW, tBase}
    locked_hash TEXT,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS contour_reviews (
    id              TEXT PRIMARY KEY,
    session_id      TEXT NOT NULL,
    fraction_index  INTEGER NOT NULL,
    structure_id    TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'pending'  -- pending|accepted|rejected
);
"""

async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript(CREATE_TABLES)
        await db.commit()
```

---

## routers/slices.py — binary hot path

This is the most performance-sensitive route. Every MPR scroll fires it.

```python
import json
from fastapi import APIRouter
from fastapi.responses import Response
from services.slice_service import get_slice_bytes

router = APIRouter()

@router.get("/datasets/{dataset_id}/slice")
async def get_slice(
    dataset_id: str,
    axis: str,          # axial | coronal | sagittal
    index: int,
    lod: str = "native",
):
    buf, width, height, min_val, max_val = await get_slice_bytes(
        dataset_id, axis, index, lod
    )
    meta = json.dumps({"width": width, "height": height, "min": min_val, "max": max_val})
    return Response(
        content=buf,
        media_type="application/octet-stream",
        headers={"X-Slice-Meta": meta},
    )
```

```python
# services/slice_service.py
import asyncio
import numpy as np
from pycdms import DataCatalogue   # adjust to actual pycdms import path

catalogue = DataCatalogue()        # initialised once at import time

async def get_slice_bytes(dataset_id, axis, index, lod):
    # Run the synchronous DICOM/numpy operation in a thread pool so the
    # asyncio event loop is not blocked during file I/O.
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(
        None, _extract_slice, dataset_id, axis, index
    )
    return result

def _extract_slice(dataset_id, axis, index):
    vol = catalogue.load_volume(dataset_id)   # returns numpy array (Z, Y, X)
    if axis == "axial":
        plane = vol[index, :, :]
    elif axis == "coronal":
        plane = vol[:, index, :]
    else:
        plane = vol[:, :, index]
    plane = plane.astype(np.int16)
    return (
        plane.tobytes(),
        plane.shape[1],   # width
        plane.shape[0],   # height
        int(plane.min()),
        int(plane.max()),
    )
```

---

## jobs/worker.py — async job runner

```python
import asyncio
import json
import uuid
from datetime import datetime, timezone

import aiosqlite
from database import DB_PATH
from jobs.handlers import HANDLERS   # dict: job_type -> async callable


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
    # Fire-and-forget — result is polled via GET /jobs/:id
    asyncio.create_task(run_job(job_id, session_id, job_type, params))
    return await get_job(job_id)


async def run_job(job_id, session_id, job_type, params):
    await _set_status(job_id, "running")
    try:
        handler = HANDLERS.get(job_type)
        if handler is None:
            raise ValueError(f"Unknown job type: {job_type}")
        result = await handler(session_id, params, progress_cb=lambda p: _set_progress(job_id, p))
        await _set_status(job_id, "completed", result=result)
    except Exception as exc:
        await _set_status(job_id, "failed", message=str(exc))


async def _set_status(job_id, status, message=None, result=None):
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE jobs SET status=?, message=?, result=?, updated_at=? WHERE id=?",
            (status, message, json.dumps(result) if result else None, now, job_id),
        )
        await db.commit()

async def _set_progress(job_id, progress: float):
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE jobs SET progress=?, updated_at=? WHERE id=?",
            (progress, now, job_id),
        )
        await db.commit()

async def get_job(job_id: str) -> dict:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None
```

---

## jobs/handlers.py — engine calls per job type

```python
import asyncio
from services.dose_service import run_dose_calc, run_phantom_calc
from services.deform_service import run_registration, run_dose_accumulation


async def handle_dose_calc(session_id, params, progress_cb):
    loop = asyncio.get_event_loop()
    # GenDoseCalc is CPU/GPU-bound — offload to thread pool
    result = await loop.run_in_executor(
        None, run_dose_calc, session_id, params
    )
    await progress_cb(1.0)
    return result


async def handle_register(session_id, params, progress_cb):
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(
        None, run_registration, session_id, params
    )
    await progress_cb(1.0)
    return result


async def handle_dose_accumulation(session_id, params, progress_cb):
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(
        None, run_dose_accumulation, session_id, params
    )
    await progress_cb(1.0)
    return result


async def handle_phantom_calc(session_id, params, progress_cb):
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(
        None, run_phantom_calc, params
    )
    await progress_cb(1.0)
    return result


HANDLERS = {
    "dose-calc":          handle_dose_calc,
    "register":           handle_register,
    "dose-accumulation":  handle_dose_accumulation,
    "phantom-calc":       handle_phantom_calc,
    # dvh and gamma are post-processing of dose output — add when ready
}
```

---

## services/dose_service.py — GenDoseCalc wrapper

```python
# Adjust import path to match GenDoseCalc's actual package name
from gendosecalc.core import DoseEngine, WaterPhantomMode


def run_dose_calc(session_id: str, params: dict):
    """Synchronous — called via run_in_executor."""
    engine = DoseEngine(session_id=session_id)
    result = engine.calculate(**params)
    return {"dose_dataset_id": result.output_path}


def run_phantom_calc(params: dict):
    """Water-phantom PDD/profile calculation for commissioning."""
    engine = WaterPhantomMode(
        machine_id=params["machineId"],
        engine=params["engine"],
        parameters=params["parameters"],
    )
    result = engine.run()
    return {
        "pdd":           result.pdd,
        "profileDmax":   result.profile_dmax,
        "profile10cm":   result.profile_10cm,
        "outputFactors": result.output_factors,
    }
```

---

## services/deform_service.py — DeformCT wrapper

```python
from deformct.core import DeformableRegistration


def run_registration(session_id: str, params: dict):
    """Synchronous — called via run_in_executor."""
    reg = DeformableRegistration(session_id=session_id)
    result = reg.register_all_fractions(**params)
    # result.registrations: list of {fractionIndex, rmsSurfaceDistanceMm, meanDice}
    return {"registrations": result.registrations}


def run_dose_accumulation(session_id: str, params: dict):
    reg = DeformableRegistration(session_id=session_id)
    result = reg.accumulate_dose(**params)
    return {
        "patientId":                result.patient_id,
        "includedFractionIndices":  result.included_fractions,
        "totalPrescriptionGy":      result.prescription_gy,
        "structures":               result.structures,
    }
```

---

## Development workflow

```bash
# 1. Clone repos as siblings
cd ~/repos
git clone <url>/DeformCT
git clone <url>/GenDoseCalc
git clone <url>/pycdms        # or: pip install pycdms if on PyPI
git clone <url>/axioma-sidecar

# 2. Create a single venv for the sidecar (installs engines as editable)
cd axioma-sidecar
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 3. Run the sidecar
uvicorn main:app --port 8000 --reload

# 4. Run the frontend (separate terminal)
cd ~/repos/AxiomaUX
VITE_API_BASE_URL=http://localhost:8000 npm run dev
# → http://localhost:1420

# 5. Edit engine code without reinstalling
#    Changes in ../DeformCT/deformct/core.py are live immediately.
```

---

## Tauri integration (desktop packaging)

When building for distribution, Tauri spawns the sidecar as a child process.
In `AxiomaUX/src-tauri/tauri.conf.json`:

```json
{
  "bundle": {
    "externalBin": ["../axioma-sidecar/bin/axioma-sidecar"]
  }
}
```

The sidecar is built into a single executable via PyInstaller or `nuitka` before
packaging. The editable installs become bundled code at build time — the
`-e` flags are only for the development workflow.

---

## Completion checklist

- [ ] `axioma-sidecar` repo created, `pyproject.toml` set up
- [ ] `requirements.txt` with `-e` editable installs confirmed working
- [ ] `database.py` — SQLite tables created on startup
- [ ] `routers/patients.py` + `routers/sessions.py` — pycdms catalogue wired
- [ ] `routers/slices.py` — binary slice stream returning `X-Slice-Meta` header
- [ ] `routers/jobs.py` + `jobs/worker.py` — async queue functional
- [ ] `jobs/handlers.py` — dose-calc and register handlers calling real engines
- [ ] `routers/commissioning.py` — phantom calc + machine lock
- [ ] `routers/adaptive.py` — registration results + contour status PATCH
- [ ] Frontend `VITE_USE_MOCK=false` smoke test passes end-to-end
- [ ] Tauri sidecar binary build verified
