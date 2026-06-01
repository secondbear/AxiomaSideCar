# Integration Test Report — 2026-06-01

**Suite:** `LocalDataGateway — live sidecar contract`
**File:** `src/gateway/LocalDataGateway.integration.test.ts`
**Env:** `VITE_API_BASE_URL=http://localhost:8000 VITE_USE_MOCK=false`
**Result:** 14 passed · **4 failed**

---

## Summary table

| # | Test | Owner repo | Severity |
|---|------|-----------|----------|
| 1 | `datasets > mountDataset rejects for non-existent path` | **AxiomaSideCar** | High |
| 2 | `commissioning > calculateWaterPhantom returns WaterPhantomResult shape` | **AxiomaUX** + **AxiomaSideCar** | High |
| 3 | `adaptive > listRegistrationResults returns an array` | **AxiomaSideCar** | High |
| 4 | `adaptive > updateContourStatus 404 rejects for unknown contour` | **AxiomaSideCar** | Medium |

---

## Issue 1 — `mountDataset` accepts non-existent paths silently

**Owner:** AxiomaSideCar
**File:** `services/session_service.py` → `mount_dataset()`

### Observed
`POST /api/v1/sessions/{id}/datasets/mount` with `patient_data_path: "/does/not/exist"` returns **201** with a dataset record:
```json
{ "id": "...", "session_id": "...", "path": "/does/not/exist", "content_type": "unknown", "file_count": 0 }
```

### Root cause
`pycdms.scan_folder()` is called on the non-existent path. It returns an empty list rather than raising. The service then writes the record with `content_type="unknown"` and `file_count=0` without checking whether the path exists.

```python
# services/session_service.py line 100
files = await loop.run_in_executor(None, scan_folder, Path(patient_data_path))
# No existence check — proceeds to INSERT even when files == []
```

### Required fix (AxiomaSideCar)
Add a path-existence guard before invoking `scan_folder`:

```python
async def mount_dataset(session_id: str, patient_data_path: str) -> dict:
    path = Path(patient_data_path)
    if not path.exists():
        raise HTTPException(status_code=422, detail=f"Path does not exist: {patient_data_path}")
    # ... rest of function unchanged
```

---

## Issue 2 — `calculateWaterPhantom` payload schema mismatch

**Owner:** AxiomaUX (primary) + AxiomaSideCar (for confirmation)
**Files:**
- `src/lib/commissioning/types.ts` → `CalculatePhantomPayload`
- `src/gateway/LocalDataGateway.ts` → `calculateWaterPhantom()`

### Observed
`POST /api/v1/commissioning/calculate_water_phantom` with payload from `LocalDataGateway` returns **422**:
```
missing fields: 'engine', 'parameters'
input received: {"machineId": "test", ...}
```

### Root cause
There are two separate problems:

**A — field name mismatch (`machineId` vs `machine_id`)**

The TS `CalculatePhantomPayload` interface uses camelCase `machineId`. `LocalDataGateway` serializes it directly via `JSON.stringify(payload)`. The sidecar `PhantomCalcBody` expects snake_case `machine_id`.

| Layer | Field name |
|-------|-----------|
| `CalculatePhantomPayload` (TS) | `machineId` |
| Sidecar `PhantomCalcBody` (Python) | `machine_id` |

**B — payload shape mismatch**

The current `CalculatePhantomPayload` type exposes `{ machineId, engine, parameters: BeamModelParams }`, which is correct structurally, but the gateway must serialize `machineId` → `machine_id` on the wire.

```typescript
// src/lib/commissioning/types.ts — current (broken over the wire)
export interface CalculatePhantomPayload {
  machineId: string          // ← serializes as "machineId", sidecar expects "machine_id"
  engine: CommissioningEngine
  parameters: BeamModelParams
}
```

### Required fix (AxiomaUX)
Transform the payload in `LocalDataGateway.calculateWaterPhantom` before sending:

```typescript
// src/gateway/LocalDataGateway.ts
async calculateWaterPhantom(payload: CalculatePhantomPayload): Promise<WaterPhantomResult> {
  return apiFetch<WaterPhantomResult>('/api/v1/commissioning/calculate_water_phantom', {
    method: 'POST',
    body: JSON.stringify({
      machine_id: payload.machineId,
      engine: payload.engine,
      parameters: payload.parameters,
    }),
  })
}
```

### Confirmation needed (AxiomaSideCar)
Verify `PhantomCalcBody.parameters` accepts the `BeamModelParams` shape (`mu`, `sigmaP`, `sigmaW`, `tBase`) and document the expected schema in `schemas.py` or the OpenAPI description.

---

## Issue 3 — `listRegistrationResults` crashes with 500

**Owner:** AxiomaSideCar
**File:** `routers/adaptive.py` → `get_registrations()`

### Observed
`GET /api/v1/adaptive/sessions/{any-id}/registrations` always returns **500 Internal Server Error**.

### Root cause
The endpoint unconditionally invokes `run_registration()` — the live `DeformCTMovement` / `GenDoseCalc` engine — with empty params on every GET request:

```python
# routers/adaptive.py line 19-21
@router.get("/adaptive/sessions/{session_id}/registrations")
async def get_registrations(session_id: str):
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, run_registration, session_id, {})
    # ↑ crashes immediately — engine requires ct_dir, motion_path, out_dir
```

This confuses a **read** endpoint (listing stored results) with a **compute** operation. The engine is invoked with `{}` params, which causes it to fail before doing any work.

### Required fix (AxiomaSideCar)
The GET endpoint should query stored registration results from the DB (populated by a previously run `register` job), not invoke the engine:

```python
@router.get("/adaptive/sessions/{session_id}/registrations")
async def get_registrations(session_id: str):
    # Query DB for registration results linked to this session
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM registration_results WHERE session_id=?", (session_id,)
        ) as cur:
            rows = await cur.fetchall()
    return [dict(r) for r in rows]
```

If `registration_results` doesn't exist as a table yet, it should be created in `database.py` and populated by the `register` job worker.

---

## Issue 4 — `updateContourStatus` upserts instead of 404-ing on unknown contour

**Owner:** AxiomaSideCar
**File:** `routers/adaptive.py` → `update_contour_status()`

### Observed
`PATCH /api/v1/adaptive/contours/nonexistent/status` with `{ "status": "accepted" }` returns **200** with a new contour review record created for the unknown ID. Expected: **404**.

### Root cause
The endpoint uses upsert semantics — if the contour review doesn't exist it auto-creates one:

```python
if row is None:
    # Auto-create the review record if it doesn't exist yet   ← intent unclear
    await db.execute(
        "INSERT INTO contour_reviews (id, session_id, fraction_index, structure_id, status) "
        "VALUES (?, '', 0, '', ?)",
        (contour_id, body.status),
    )
```

### Decision required (AxiomaSideCar)
This is a **design decision**, not a clear bug. Two options:

**Option A — Strict mode (preferred for data integrity):** Require the contour review to exist before it can be updated. Contour reviews should be created by the adaptive workflow, not by the status-update endpoint.
```python
if row is None:
    raise HTTPException(status_code=404, detail=f"Contour review {contour_id} not found")
```

**Option B — Intentional upsert:** If the auto-create behaviour is by design (e.g. reviewers can mark any contour without a prior DB row), document it explicitly and update the test expectation to reflect this contract.

---

## Passing tests (for reference)

| Test | Status |
|------|--------|
| `patients > listPatients returns an array` | ✅ |
| `patients > getPatient 404 rejects` | ✅ |
| `sessions > createSession returns all required fields` | ✅ |
| `sessions > listSessions filtered by patientId` | ✅ |
| `sessions > getSession 404 rejects` | ✅ |
| `datasets > listDatasets returns array for new session` | ✅ |
| `jobs > listJobs returns array for new session` | ✅ |
| `jobs > triggerJob returns queued status` | ✅ |
| `jobs > getJob returns the enqueued job` | ✅ |
| `jobs > getJob 404 rejects` | ✅ |
| `commissioning > listMachines returns an array` | ✅ |
| `commissioning > lockMachine 404 for unknown machine` | ✅ |
| `slices > getSlice 404 for unknown dataset` | ✅ |
| `adaptive > getDoseAccumulation 404 for session with no accumulation` | ✅ |

---

## Reproduction

```bash
# 1. Boot sidecar
cd ~/repos/AxiomaSideCar && source .venv/bin/activate && uvicorn main:app --port 8000

# 2. Seed test patient (one-time)
python3 -c "
import sqlite3, uuid
from datetime import timezone, datetime
conn = sqlite3.connect('axioma.db')
pid = str(uuid.uuid4())
now = datetime.now(timezone.utc).isoformat()
conn.execute('INSERT INTO patients (id, external_id, name, dob, created_at) VALUES (?,?,?,?,?)',
    (pid, 'TEST-001', 'Integration Test', '1980-01-01', now))
conn.commit(); print('patient_id=' + pid)
"

# 3. Run suite
cd ~/repos/AxiomaUX
NODE=/home/mladmin5/.vscode-server/bin/6a49527b96e326fe62fbdb56f60e16877c9aa724/node
VITE_API_BASE_URL=http://localhost:8000 VITE_USE_MOCK=false \
  $NODE node_modules/.bin/vitest run --reporter=verbose src/gateway/LocalDataGateway.integration.test.ts
```
