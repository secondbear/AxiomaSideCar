# Skill: Jobs Queue

## When to use this skill
When adding new job types, modifying `jobs/handlers.py`, `jobs/worker.py`,
or any route that enqueues or polls jobs.

---

## Job lifecycle

```
POST /api/v1/sessions/{session_id}/jobs  →  enqueue_job()  →  status: "queued"
                                                ↓
                                        asyncio.create_task(_run_job())
                                                ↓
                                        status: "running"
                                                ↓
                                        HANDLERS[job_type](session_id, params, progress_cb)
                                                ↓
                                   ┌── success ──┐── failure ──┐
                               "completed"             "failed"
                               result=JSON             message=str(exc)

GET /api/v1/jobs/{job_id}  →  poll current status, progress, result
```

All state is persisted to the `jobs` table in SQLite. `progress` is a REAL (0.0–1.0).

---

## SQLite jobs table schema

```sql
CREATE TABLE IF NOT EXISTS jobs (
    id          TEXT PRIMARY KEY,
    session_id  TEXT NOT NULL,
    type        TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'queued',   -- queued|running|completed|failed
    progress    REAL NOT NULL DEFAULT 0.0,        -- 0.0 to 1.0
    message     TEXT,                             -- error message on failure
    params      TEXT,                             -- JSON blob (input)
    result      TEXT,                             -- JSON blob (output, set on completion)
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
```

---

## Handler signature

Every handler in `HANDLERS` must match this exact signature:

```python
async def handle_my_job(session_id: str, params: dict, progress_cb) -> dict:
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, my_sync_service_fn, session_id, params)
    await progress_cb(1.0)
    return result  # must be JSON-serialisable dict
```

- `progress_cb` is an async callable: `await progress_cb(0.5)` writes 50% to SQLite.
- Engine calls are synchronous — always wrap in `run_in_executor`.
- Return value is stored as JSON in the `result` column.

---

## Registering a new job type

1. Add a service function in `services/` that makes the engine call (sync).
2. Add a handler in `jobs/handlers.py` following the signature above.
3. Add the handler to the `HANDLERS` dict:

```python
# jobs/handlers.py
HANDLERS: dict = {
    "dose-calc":         handle_dose_calc,
    "register":          handle_register,
    "dose-accumulation": handle_dose_accumulation,
    "phantom-calc":      handle_phantom_calc,
    "dvh-calc":          handle_dvh_calc,      # ← add here
    "gamma-calc":        handle_gamma_calc,    # ← add here
}
```

The frontend enqueues jobs by `type` string — the key in `HANDLERS` is the
canonical job type name used in `POST /api/v1/sessions/:id/jobs`.

---

## Currently implemented job types

| type | service call | returns |
|------|-------------|---------|
| `dose-calc` | `services/dose_service.run_dose_calc` | `{maxDoseGy, meanDoseGy, provenance}` |
| `register` | `services/deform_service.run_registration` | `{manifest, out_dir}` |
| `dose-accumulation` | `services/deform_service.run_dose_accumulation` | `DeformableDoseReport.as_dict()` |
| `phantom-calc` | `services/dose_service.run_phantom_calc` | `{maxDoseGy, meanDoseGy, nProjections, provenance}` |

## Planned but not yet wired

| type | service call | depends on |
|------|-------------|-----------|
| `dvh-calc` | `gendosecalc.analysis.dvh.compute_dvh` | requires `dose_grid` + RTSTRUCT path in params |
| `gamma-calc` | `gendosecalc.analysis.gamma.compute_gamma` | requires two `DoseGrid` objects — reference and evaluation |

For `dvh-calc` and `gamma-calc`, the service function must load the persisted
`DoseGrid` from a previous `dose-calc` or `dose-accumulation` job result, then
call the analysis function. Loading/saving `DoseGrid` objects to disk should use
`numpy.save` / `numpy.load` with a path stored in the job `result` JSON.

---

## params conventions for engine jobs

All file paths in `params` must be **absolute** strings. The frontend sends
paths from its file picker; the sidecar does not resolve relative paths.

```json
{
  "rtplan_path":   "/data/patient_001/RP.dcm",
  "ct_dir":        "/data/patient_001/CT",
  "motion_path":   "/data/patient_001/MotionData.xml",
  "out_dir":       "/data/patient_001/deformed_ensemble",
  "target_spacing_mm": 2.5
}
```

---

## Error handling

If a handler raises any exception, `_run_job` catches it, sets `status="failed"`,
and stores `str(exc)` in the `message` column. No special error types needed —
the worker catches `Exception` broadly so the job always reaches a terminal state.
Do not let engine exceptions propagate past the handler.
