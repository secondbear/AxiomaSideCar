# AxiomaSideCar — Implementation Plan

## Overview
Turn the stubbed FastAPI scaffold into a working orchestrator that bridges AxiomaUX (Tauri/React)
with three engine repos: **DeformCTMovement**, **GenDoseCalc**, and **pycdms**. Organised into
independently-verifiable phases.

---

## Phase 1 — Environment & Engine Wiring ✅

### Step 1 — Install engines as editable siblings ✅
Actual sibling layout (repo names corrected from original plan):

```
~/repos/
├── AxiomaSideCar/        ← this repo
├── DeformCTMovement/     ← was: DeformCT (wrong name)
├── GenDoseCalc/
│   └── GenDoseCalc/      ← installable package is one level deeper
└── pycdms/
```

`requirements.txt` editable paths:
```
-e ../DeformCTMovement
-e ../GenDoseCalc/GenDoseCalc
-e ../pycdms
```

Python 3.11 required (`DeformCTMovement` declares `requires-python >= 3.11`).
venv created with `python3.11 -m venv .venv`.

**Verify:**
```bash
source .venv/bin/activate
python -c "import gendosecalc, pycdms; print('engines OK')"
```

### Step 2 — Config & settings ✅
- Add `config.py` using `pydantic-settings`: exposes `DB_PATH`, `CORS_ORIGIN`,
  optional `ENGINE_DATA_ROOT`.
- Add `.env.example` with commented defaults.
- Wire `main.py` CORS and `database.py` `DB_PATH` to read from config.

---

## Phase 2 — Typed Contracts
*Depends on Phase 1*

### Step 3 — Pydantic schemas ✅
Add `schemas.py` with models matching the frontend contracts:

| Schema | Key fields |
|--------|------------|
| `Patient` | id, externalId, name, dob, createdAt |
| `Session` | id, patientId, label, createdAt, updatedAt |
| `DatasetMeta` | id, sessionId, path, contentType, fileCount, createdAt |
| `JobStatus` | id, sessionId, type, status, progress, message, result, createdAt, updatedAt |
| `MachineRecord` | id, name, engine, status, params, lockedHash, createdAt, updatedAt |
| `RegistrationResult` | manifest, outDir |
| `DoseResult` | maxDoseGy, meanDoseGy, nProjections, provenance |
| `AccumulatedDoseResult` | nStates, totalWeight, accumulatedMaxGy, accumulatedMeanGy, totalElapsedS |

Annotate router return types so FastAPI generates correct OpenAPI at `/docs`.

### Step 4 — Wire pycdms to session_service ✅
Done. `services/session_service.py` uses SQLite for all patient/session/dataset CRUD.
`pycdms.scan_folder()` is called only in `mount_dataset()` to classify archive files.
`pycdms.DataCatalogue` does not exist — pycdms is a file format parser only.

---

## Phase 3 — Slice Hot Path ✅

### Step 5 — Implement slice_service ✅
`services/slice_service.py` loads DICOM CT series with pydicom, applies
RescaleSlope/RescaleIntercept, and returns raw int16 bytes with `X-Slice-Meta` header.
Dataset path is looked up from the `datasets` SQLite table.
`_load_ct_volume` is decorated with `@lru_cache(maxsize=4)` — repeated MPR scroll
requests for the same dataset hit the in-process cache instead of reloading from disk.

---

## Phase 4 — Jobs & Engine Services

### Step 6 — Finish job worker ✅
All six job types implemented in `jobs/handlers.py`. DVH and gamma handlers
compute dose from params directly (no intermediate file needed):
- `dvh-calc` → `ClinicalRunContext` + `compute_dvh` + `load_structure_masks_from_rtstruct`
- `gamma-calc` → planned static dose vs motion-corrected dose → `compute_gamma`
Job results are persisted to the `result` column in SQLite on completion.

Required params per type are documented in `.github/skills/jobs-queue/SKILL.md`.

### Step 7 — Real engine service calls ✅
All three service files rewritten to use real engine APIs:

| File | Was (wrong) | Now (correct) |
|------|-------------|---------------|
| `services/deform_service.py` | `deformct.core.DeformableRegistration` | `gendosecalc.deform.generate_ensemble` + `compute_deformable_dose` |
| `services/dose_service.py` | `gendosecalc.core.DoseEngine` | `ClinicalRunContext.build().compute_planned_static()` + `compute_motion_dose` |
| `services/session_service.py` | `pycdms.DataCatalogue` | SQLite CRUD + `pycdms.scan_folder` |
| `services/slice_service.py` | `pycdms.DataCatalogue.load_volume` | pydicom direct load |

---

## Phase 5 — Commissioning & Adaptive ✅

### Step 8 — Commissioning router ✅
Full machine CRUD, file upload, water-phantom calc, SHA-256 lock.
Write-through sync: every `POST /machines` and `POST /lock` also calls
`gendosecalc.service.machines` to keep `machines.yaml` in sync.
GenDoseCalc YAML is the ground truth for `ClinicalRunContext.build()` auto-selection.

### Step 9 — Adaptive router ✅
- `GET /adaptive/sessions/:id/registrations` → calls `run_registration` (deform engine)
- `PATCH /adaptive/contours/:id/status` → upserts `contour_reviews` table
- `GET /adaptive/sessions/:id/dose-accumulation` → calls `run_dose_accumulation`

---

## Phase 6 — Tests & CI

### Step 10 — pytest suite ✅
`tests/` created with four test modules (26 tests, all passing):

| Module | Coverage |
|--------|----------|
| `test_patients_sessions.py` | Patient/session CRUD, dataset mount, schema fields |
| `test_jobs.py` | Enqueue, poll, lifecycle, all 6 job types (dose, register, accumulation, phantom, dvh, gamma) |
| `test_slices.py` | Binary slice response + `X-Slice-Meta` header, 404 on unknown dataset |
| `test_commissioning.py` | Machine CRUD, lock (SHA-256), file upload, water-phantom calc |
| `test_adaptive.py` | Contour review PATCH — accepted/rejected/invalid/idempotent |

`conftest.py` uses a per-test temp-file SQLite DB (not `:memory:` which creates
a new DB per connection). Engine calls are monkeypatched; no patient data or GPU needed.

Side-fixes discovered by tests:
- `create_session` and `mount_dataset` in `session_service.py` now return snake_case keys
  to match `Session` / `DatasetMeta` schemas
- Slice router now returns HTTP 404 for unknown dataset IDs

### Step 11 — GitHub Actions CI ✅
`.github/workflows/ci.yml` created:
- Triggers on push and PR to `main`.
- Steps: checkout → setup Python 3.11 → `pip install` all real deps (no `-e` sibling paths)
  → `pip install -e ci-stubs/gendosecalc-stub -e ci-stubs/pycdms-stub`
  → `ruff check .` → `pytest -q`.
- `ci-stubs/gendosecalc-stub/` — minimal stubs for all 5 gendosecalc sub-packages
  (`motion`, `deform`, `io`, `plan`, `analysis`, `service`) that satisfy top-level imports.
- `ci-stubs/pycdms-stub/` — minimal `scan_folder` stub.
- All 26 tests verified passing locally with stubs installed.

---

## Phase 7 — Git Hooks ✅

### Step 12 — pre-commit framework ✅
`.pre-commit-config.yaml` created and installed. Both hook stages active:
- `pre-commit`: trailing-whitespace, end-of-file-fixer, check-yaml, ruff --fix, ruff-format
- `pre-push`: `pytest -q`

`pyproject.toml` has `[tool.ruff]` config (`target-version=py311`, `select=E,F,I,UP,B,SIM`).

---

## Phase 8 — Copilot Skills ✅

### Step 13 — SKILL.md files ✅
`.github/skills/` contains three domain-knowledge files:

| Skill | Covers |
|-------|--------|
| `axioma-engine-integration/SKILL.md` | Real import paths, call signatures, run_in_executor pattern, wrong imports to avoid |
| `slice-hotpath/SKILL.md` | X-Slice-Meta header shape, LRU cache, axis mapping, HU correction |
| `jobs-queue/SKILL.md` | Handler signature, HANDLERS dict, SQLite schema, dvh/gamma wiring guide |

---

## Phase 9 — Packaging (Optional, last)

### Step 14 — PyInstaller spec ✅
`sidecar.spec` and `sidecar_entry.py` created:
- `sidecar_entry.py` — CLI entry point; accepts `--host`, `--port`, `--reload`; calls `uvicorn.run("main:app", ...)`.
- `sidecar.spec` — one-file build spec (binaries + data embedded in EXE, no COLLECT step).
  - Hidden imports cover all uvicorn dynamic protocol/loop backends, anyio backends,
    pydantic v2, aiosqlite, multipart.
  - Excludes: tkinter, matplotlib, IPython, pytest, ruff (~40 MB saved).
  - Build command: `pyinstaller sidecar.spec --distpath axioma-sidecar/bin`
  - Output: `axioma-sidecar/bin/axioma-sidecar` — matches Tauri `externalBin` path.

---

## Verification Checklist

- [x] `python -c "import gendosecalc, pycdms; print('engines OK')"` — passes
- [x] All service imports load clean: `python -c "import main; print('app OK')"`
- [x] `pre-commit run --all-files` — all hooks pass
- [x] `config.py` + `.env.example` — typed settings wired to `main.py` and `database.py`
- [x] `schemas.py` — all routes annotated with `response_model`
- [x] `_load_ct_volume` has `@lru_cache(maxsize=4)`
- [x] `dvh-calc` and `gamma-calc` handlers registered in `HANDLERS`
- [ ] `uvicorn main:app --port 8000 --reload` boots clean; `/docs` shows all typed routes
- [ ] `GET /api/v1/datasets/{id}/slice?axis=axial&index=120` returns binary + `X-Slice-Meta`
- [ ] POST a job → poll → status reaches `completed`
- [x] `pytest -q` — 26 tests, all passing locally
- [x] Tauri sidecar binary builds and boots

---

## Decisions & Scope

- **Engines** are real sibling repos installed with `-e`; Python 3.11 required
- **pycdms** is a file-format parser only — not a patient/session store
- **Machine registry** ground truth is GenDoseCalc's `machines.yaml`; sidecar syncs via write-through
- **DVH/gamma** stay in `gendosecalc.analysis` — no separate repo needed
- **Tests** monkeypatch engine fixtures — CI needs no patient data or GPU
- **Excluded for now:** auth/multi-user, frontend integration smoke test


## Step Dependency Graph

```
Phase 1 (Steps 1-2)
    └── Phase 2 (Steps 3-4)
            ├── Phase 3 (Step 5)    ── parallel ──┐
            ├── Phase 4 (Steps 6-7) ── parallel ──┤
            └── Phase 5 (Steps 8-9) ─ depends 3+4─┘
                    └── Phase 6 (Steps 10-11)
                            └── Phase 7 (Step 12)
                                    └── Phase 8 (Step 13)
                                            └── Phase 9 (Step 14, optional)
```
