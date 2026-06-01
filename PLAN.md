# AxiomaSideCar — Implementation Plan

## Overview
Turn the stubbed FastAPI scaffold into a working orchestrator that bridges AxiomaUX (Tauri/React)
with three engine repos: **DeformCT**, **GenDoseCalc**, and **pycdms**. Organised into
independently-verifiable phases.

---

## Phase 1 — Environment & Engine Wiring

### Step 1 — Install engines as editable siblings  ← DO THIS FIRST
The three engine repos must be cloned as siblings of this repo under `~/repos/`:

```
~/repos/
├── AxiomaSideCar/   ← this repo
├── DeformCT/
├── GenDoseCalc/
└── pycdms/          (or: pip install pycdms if published on PyPI)
```

Create the venv and install everything:

```bash
cd ~/repos/AxiomaSideCar
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

The `-e ../DeformCT`, `-e ../GenDoseCalc`, `-e ../pycdms` lines in `requirements.txt` resolve
directly to the sibling dirs. Edits to engine code are live immediately — no reinstall needed.

**Verify:**
```bash
python -c "import deformct, gendosecalc, pycdms; print('engines OK')"
```

### Step 2 — Config & settings
- Add `config.py` using `pydantic-settings`: exposes `DB_PATH`, `CORS_ORIGIN`,
  optional `ENGINE_DATA_ROOT`.
- Add `.env.example` with commented defaults.
- Wire `main.py` CORS and `database.py` `DB_PATH` to read from config.

---

## Phase 2 — Typed Contracts
*Depends on Phase 1*

### Step 3 — Pydantic schemas
Add `schemas.py` with models matching the frontend contracts defined in `backend_multi_axiomics.md`:

| Schema | Key fields |
|--------|------------|
| `Patient` | id, name, dob, cohort |
| `Session` | id, patientId, label, createdAt |
| `DatasetMeta` | id, sessionId, path, modality, fractionIndex |
| `JobStatus` | id, sessionId, type, status, progress, message, createdAt, updatedAt |
| `WaterPhantomResult` | pdd, profileDmax, profile10cm, outputFactors |
| `RegistrationResult` | fractionIndex, rmsSurfaceDistanceMm, meanDice, approved |
| `AccumulatedDoseResult` | patientId, includedFractionIndices, totalPrescriptionGy, structures |

Annotate router return types so FastAPI generates correct OpenAPI at `/docs`.

### Step 4 — Wire pycdms to session_service
Replace placeholder calls in `services/session_service.py` with the real `pycdms` public API.
**Confirm actual method names against the `pycdms` repo source before writing.**

---

## Phase 3 — Slice Hot Path
*Can run in parallel with Phase 4*

### Step 5 — Implement slice_service
- Wire `services/slice_service.py` to real pycdms volume loading.
- Keep `run_in_executor` offload — this route fires on every MPR scroll event.
- Add a small LRU / `numpy.memmap` volume cache so repeated requests don't reload from disk.
- Confirm `X-Slice-Meta` response header shape in `routers/slices.py`:
  ```
  X-Slice-Meta: {"width": 512, "height": 512, "min": -1024, "max": 3071}
  ```

---

## Phase 4 — Jobs & Engine Services
*Can run in parallel with Phase 3*

### Step 6 — Finish job worker
- Add missing `dvh` and `gamma` handlers in `jobs/handlers.py`.
- Persist job results to the `result` column in SQLite on completion.
- Confirm `progress_cb` propagates correctly from long-running engine calls.

### Step 7 — Real engine service calls
- `services/dose_service.py` — wire `DoseEngine` + `WaterPhantomMode` to real GenDoseCalc API.
  Confirm class/method names from the GenDoseCalc repo.
- `services/deform_service.py` — wire `DeformableRegistration` to real DeformCT API.
  Confirm `register_all_fractions`, `accumulate_dose` method signatures.

---

## Phase 5 — Commissioning & Adaptive
*Depends on Phases 3 + 4*

### Step 8 — Commissioning router
- Machine CRUD: full `GET`, `POST`, `PUT`, `DELETE` in `routers/commissioning.py`.
- CSV / IBA / PTW measurement file parsing in the `/upload` route.
- SHA-256 lock: hash `{id + params}`, store `locked_hash`, set status → `locked`.

### Step 9 — Adaptive router
- Registration list: `GET /adaptive/sessions/:id/registrations` — DeformCT results per fraction.
- Contour review PATCH: validate status ∈ {pending, accepted, rejected}, upsert `contour_reviews`.
- Dose accumulation: `GET /adaptive/sessions/:id/dose-accumulation` — DeformCT DVF warp → GenDoseCalc DVH rollup.

---

## Phase 6 — Tests & CI

### Step 10 — pytest suite
- Add `tests/` + `conftest.py`.
- Use FastAPI `TestClient` (httpx) for all routes.
- Fixtures monkeypatch the three engines — CI needs no real patient data or GPU.
- Cover: patient/session/dataset routes, slice route (mocked volume), job lifecycle
  (enqueue → poll → completed), commissioning lock, contour PATCH.

### Step 11 — GitHub Actions CI
Add `.github/workflows/ci.yml`:
- Triggers on push and PR to `main`.
- Steps: checkout → setup Python 3.11 → install deps (lightweight stubs, no real engines in CI)
  → `ruff check .` → `pytest -q`.

---

## Phase 7 — Git Hooks

### Step 12 — pre-commit framework
Add `.pre-commit-config.yaml` with:
- `ruff` — lint on every commit
- `ruff-format` — auto-format on every commit
- `trailing-whitespace` + `end-of-file-fixer` (pre-commit built-ins)
- `pytest -q` — on `pre-push` stage only (fast with mocks, no real data needed)

Install after creating the file:
```bash
pip install pre-commit
pre-commit install --install-hooks -t pre-commit -t pre-push
```

---

## Phase 8 — Copilot Skills

### Step 13 — Author SKILL.md files
Add `.github/skills/` with three domain-knowledge files for the Copilot agent:

| Skill file | Covers |
|------------|--------|
| `axioma-engine-integration/SKILL.md` | How to wrap DeformCT / GenDoseCalc / pycdms, `run_in_executor` pattern, editable install conventions |
| `slice-hotpath/SKILL.md` | Binary slice route rules, `X-Slice-Meta` header shape, volume cache strategy, perf constraints |
| `jobs-queue/SKILL.md` | Job handler signature, SQLite lifecycle, progress callback pattern, HANDLERS dict extension |

---

## Phase 9 — Packaging (Optional, last)

### Step 14 — PyInstaller spec
Add `sidecar.spec` to build the single-file binary Tauri expects:

```
axioma-sidecar/bin/axioma-sidecar
```

Referenced in `AxiomaUX/src-tauri/tauri.conf.json` as `externalBin`. The `-e` editable
installs become bundled code at PyInstaller build time.

---

## Verification Checklist

- [ ] `python -c "import deformct, gendosecalc, pycdms"` prints `engines OK`  ← Phase 1 Step 1
- [ ] `uvicorn main:app --port 8000 --reload` boots clean; `/docs` shows all typed routes
- [ ] `GET /api/v1/datasets/{id}/slice?axis=axial&index=120` returns `application/octet-stream` with `X-Slice-Meta` header
- [ ] POST a job → poll `GET /api/v1/jobs/{id}` → status reaches `completed`
- [ ] `pytest -q` green locally and in CI
- [ ] `pre-commit run --all-files` passes with no violations
- [ ] Tauri sidecar binary builds and boots (`bin/axioma-sidecar`)

---

## Decisions & Scope

- **Skills** = Copilot `SKILL.md` files in `.github/skills/`
- **Hooks** = git hooks via `pre-commit` framework (`pre-commit` stage + `pre-push` stage)
- **Engines** are real sibling repos installed with `-e`; Phase 1 Step 1 is the prerequisite
- **Tests** use monkeypatched engine fixtures — CI needs no patient data or GPU
- **Excluded for now:** auth / multi-user, dose-accumulation rollup detail (stub until engine API
  confirmed after Phase 1), frontend integration smoke test

---

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
