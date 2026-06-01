# Axioma Backend Integration Requirements

The frontend connects via `LocalDataGateway` to a **FastAPI sidecar on `localhost:8000`**
(configurable via `VITE_API_BASE_URL`). All endpoints below are called from
`src/gateway/LocalDataGateway.ts`.

---

## 1. Patient / Session / Dataset management

Thin data-management layer. `pycdms` likely covers this if wrapped.

| Method | Endpoint | Payload / Notes |
|--------|----------|-----------------|
| `GET`  | `/api/v1/patients` | Returns `Patient[]` |
| `GET`  | `/api/v1/patients/:id` | Returns `Patient` |
| `GET`  | `/api/v1/sessions?patient_id=` | Returns `Session[]` |
| `POST` | `/api/v1/sessions` | `{patient_id, label}` → `Session` |
| `GET`  | `/api/v1/sessions/:id` | Returns `Session` |
| `GET`  | `/api/v1/sessions/:id/datasets` | Returns `DatasetMeta[]` |
| `POST` | `/api/v1/sessions/:id/datasets/mount` | `{patient_data_path}` → `DatasetMeta` |

---

## 2. Slice streaming (MPR viewports — hot path)

Every viewport scroll fires this. Must return a **raw 2D pixel buffer as `ArrayBuffer`**
with slice metadata in an HTTP response header.

| Method | Endpoint | Returns |
|--------|----------|---------|
| `GET`  | `/api/v1/datasets/:id/slice?axis=axial&index=120&lod=native` | `ArrayBuffer` (row-major int16 or float32) |

Response header required:
```
X-Slice-Meta: {"width": 512, "height": 512, "min": -1024, "max": 3071}
```

**This is the hardest piece.** `pycdms` already parses DICOM — expose
`dataset.get_slice(axis, index)` as a FastAPI streaming route.

---

## 3. Jobs (async background work)

The `JobTriggerMenu` posts a job and the UI polls it via TanStack Query every 2 s
until `status` reaches `completed` or `failed`.

| Method | Endpoint | Notes |
|--------|----------|-------|
| `GET`  | `/api/v1/sessions/:id/jobs` | Returns `JobStatus[]` |
| `POST` | `/api/v1/sessions/:id/jobs` | `{type, params}` → `JobStatus` (queued immediately) |
| `GET`  | `/api/v1/jobs/:id` | Poll single job for progress |

### Job types and tool mapping

| Job `type` | Triggered from | Tool |
|------------|---------------|------|
| `dose-calc` | Planning | **GenDoseCalc** |
| `dvh` | Analysis | post-dose analytics (GenDoseCalc output) |
| `gamma` | Analysis | gamma analysis |
| `register` | Adaptive | **DeformCT** |
| `dose-accumulation` | Adaptive | **DeformCT** warped dose → **GenDoseCalc** DVH rollup |
| `phantom-calc` | Commissioning | **GenDoseCalc** in water-phantom mode |

`JobStatus` shape:
```json
{
  "id": "string",
  "sessionId": "string",
  "type": "dose-calc",
  "status": "queued | running | completed | failed",
  "progress": 0.0,
  "message": "optional string",
  "createdAt": "ISO8601",
  "updatedAt": "ISO8601"
}
```

---

## 4. Commissioning Studio

| Method | Endpoint | Tool / Notes |
|--------|----------|--------------|
| `GET`  | `/api/v1/commissioning/machines` | Config DB (SQLite) |
| `POST` | `/api/v1/commissioning/upload` | Multipart file upload — parse water-tank CSV/IBA/PTW |
| `POST` | `/api/v1/commissioning/calculate_water_phantom` | **GenDoseCalc** in phantom mode |
| `POST` | `/api/v1/commissioning/lock` | Sign params with SHA-256; return hash + timestamp |

`calculate_water_phantom` payload:
```json
{
  "machine_id": "string",
  "engine": "PBE | LBTE",
  "parameters": { "mu": 0.059, "sigmaP": 3.2, "sigmaW": 6.5, "tBase": 1.8 }
}
```

Returns `WaterPhantomResult`:
```json
{
  "pdd": [{"x": 0, "y": 100}, ...],
  "profileDmax": [{"x": -100, "y": 20}, ...],
  "profile10cm": [{"x": -100, "y": 18}, ...],
  "outputFactors": [{"fieldSize": 5, "sf": 0.95}, ...]
}
```

---

## 5. Adaptive workflow

| Method | Endpoint | Tool / Notes |
|--------|----------|--------------|
| `GET`  | `/api/v1/adaptive/sessions/:id/registrations` | **DeformCT** results per fraction |
| `PATCH`| `/api/v1/adaptive/contours/:id/status` | `{status: "accepted|rejected|pending"}` |
| `GET`  | `/api/v1/adaptive/sessions/:id/dose-accumulation` | **DeformCT** + **GenDoseCalc** DVH rollup |

`RegistrationResult` shape (one per fraction):
```json
{
  "fractionIndex": 1,
  "rmsSurfaceDistanceMm": 1.2,
  "meanDice": 0.91,
  "approved": false
}
```

`AccumulatedDoseResult` shape:
```json
{
  "patientId": "string",
  "includedFractionIndices": [1, 2, 3],
  "totalPrescriptionGy": 70.0,
  "structures": [
    {
      "structureId": "ptv",
      "structureName": "PTV 70",
      "role": "target",
      "nFractions": 3,
      "curve": [[0.0, 1.0], [35.0, 0.95], [70.0, 0.05]]
    }
  ]
}
```

---

## Tool mapping summary

| What you have | Covers |
|---------------|--------|
| **GenDoseCalc** | `dose-calc` job, `phantom-calc` job (water mode), final dose-accumulation step |
| **DeformCT** | `register` job (DIR per fraction), warped-dose input for accumulation, `RegistrationResult` metrics (RMS surface distance, mean Dice) |
| **pycdms** (module) | Patient/session catalogue, DICOM parsing for `mountDataset`, slice extraction for MPR viewports |

## Missing glue: thin FastAPI wrapper

A single Python service (`main.py`) that:

1. Wraps `pycdms` for patient/session/DICOM/slice routes
2. Dispatches `GenDoseCalc` and `DeformCT` as background task workers (SQLite job store, polled via `/api/v1/jobs/:id`)
3. Handles commissioning CSV upload + `calculate_water_phantom` forwarding to GenDoseCalc in phantom mode
4. Stores machine configs and contour review decisions in SQLite

No complex logic required in the API layer — all routes are thin wrappers around existing tools.
