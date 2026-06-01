# Integration Test Setup — AxiomaUX ↔ AxiomaSideCar

Copy this file to `AxiomaUX/integration_test.md`.

---

## Overview

These tests exercise `LocalDataGateway` against a real running sidecar.
They live in `AxiomaUX` because the TypeScript types (`Patient`, `Session`, etc.)
are defined there and the gateway is the seam being tested.

The sidecar's own pytest suite (26 unit tests) already covers the Python layer.
These tests cover the **HTTP contract** — field names, shapes, status codes —
from the client's point of view.

---

## Prerequisites

```bash
# 1. Boot the sidecar (keep this terminal open)
cd ~/repos/AxiomaSideCar
source .venv/bin/activate
uvicorn main:app --port 8000

# 2. In a second terminal — run the integration suite
cd ~/repos/AxiomaUX
VITE_API_BASE_URL=http://localhost:8000 VITE_USE_MOCK=false npx vitest run src/gateway/LocalDataGateway.integration.test.ts
```

The two env vars are required:
| Variable | Value | Why |
|----------|-------|-----|
| `VITE_API_BASE_URL` | `http://localhost:8000` | Points `apiFetch` at the live sidecar |
| `VITE_USE_MOCK` | `false` | Selects `LocalDataGateway` in `gateway/index.ts` |

---

## Test file location

```
AxiomaUX/src/gateway/LocalDataGateway.integration.test.ts
```

Vitest picks it up automatically (matches `**/*.test.ts`).

---

## Test scaffold

Create `src/gateway/LocalDataGateway.integration.test.ts`:

```typescript
import { describe, it, expect, beforeAll } from 'vitest'
import { LocalDataGateway } from './LocalDataGateway'

const gw = new LocalDataGateway()

// ── helpers ────────────────────────────────────────────────────────────────

let createdPatientId: string   // populated by direct sidecar call (no gateway method yet)
let createdSessionId: string

async function seedPatient(): Promise<string> {
  // The sidecar has no POST /patients endpoint — insert via its test helper
  // or use the SQLite DB directly. Simplest: call the sidecar REST directly.
  const res = await fetch('http://localhost:8000/api/v1/patients', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ mrn: 'TEST-001', name: 'Integration Test', date_of_birth: '1980-01-01' }),
  })
  // If no POST /patients exists, insert via a seed script instead (see below).
  if (!res.ok) throw new Error(`Seed patient failed: ${res.status}`)
  const p = await res.json()
  return p.id
}

// ── suite ──────────────────────────────────────────────────────────────────

describe('LocalDataGateway — live sidecar contract', () => {

  // ── Patients ──────────────────────────────────────────────────────────────

  describe('patients', () => {
    it('listPatients returns an array', async () => {
      const patients = await gw.listPatients()
      expect(Array.isArray(patients)).toBe(true)
    })

    it('getPatient 404 rejects with API error message', async () => {
      await expect(gw.getPatient('nonexistent-id')).rejects.toThrow('API error 404')
    })
  })

  // ── Sessions ──────────────────────────────────────────────────────────────

  describe('sessions', () => {
    beforeAll(async () => {
      // Need a real patient to create sessions against.
      // Option A: seed via direct HTTP (if POST /patients is implemented).
      // Option B: run a seed script first (see "Seed script" section below).
      // createdPatientId = await seedPatient()
    })

    it('createSession returns a Session with all required fields', async () => {
      // Requires a valid patientId — fill after seeding.
      const session = await gw.createSession(createdPatientId, 'Integration Test Session')
      createdSessionId = session.id
      expect(session).toMatchObject({
        id: expect.any(String),
        patientId: expect.any(String),
        label: 'Integration Test Session',
        createdAt: expect.any(String),
      })
    })

    it('listSessions filtered by patientId returns only matching sessions', async () => {
      const sessions = await gw.listSessions(createdPatientId)
      expect(sessions.every(s => s.patientId === createdPatientId)).toBe(true)
    })

    it('getSession 404 rejects', async () => {
      await expect(gw.getSession('bad-id')).rejects.toThrow('API error 404')
    })
  })

  // ── Datasets ──────────────────────────────────────────────────────────────

  describe('datasets', () => {
    it('listDatasets returns empty array for new session', async () => {
      const datasets = await gw.listDatasets(createdSessionId)
      expect(datasets).toEqual([])
    })

    it('mountDataset rejects for non-existent path', async () => {
      await expect(
        gw.mountDataset(createdSessionId, '/does/not/exist')
      ).rejects.toThrow()
    })
  })

  // ── Jobs ──────────────────────────────────────────────────────────────────

  describe('jobs', () => {
    it('listJobs returns empty array for new session', async () => {
      const jobs = await gw.listJobs(createdSessionId)
      expect(jobs).toEqual([])
    })

    it('triggerJob returns JobStatus with queued status', async () => {
      const job = await gw.triggerJob(createdSessionId, 'dose-calc', { dataset_id: 'fake' })
      expect(job.status).toBe('queued')
      expect(job.id).toBeTruthy()
    })

    it('getJob returns the job just enqueued', async () => {
      const job = await gw.triggerJob(createdSessionId, 'dose-calc', { dataset_id: 'fake' })
      const fetched = await gw.getJob(job.id)
      expect(fetched.id).toBe(job.id)
      expect(['queued', 'running', 'completed', 'failed']).toContain(fetched.status)
    })

    it('getJob 404 rejects', async () => {
      await expect(gw.getJob('nonexistent')).rejects.toThrow('API error 404')
    })
  })

  // ── Commissioning ─────────────────────────────────────────────────────────

  describe('commissioning', () => {
    let machineId: string

    it('listMachines returns an array', async () => {
      const machines = await gw.listMachines()
      expect(Array.isArray(machines)).toBe(true)
    })

    it('calculateWaterPhantom returns WaterPhantomResult shape', async () => {
      // The sidecar engine call is monkeypatched in unit tests; here the real
      // engine runs. If engine is unavailable, this test is expected to fail
      // with a 500 — that itself is a valid contract signal.
      const result = await gw.calculateWaterPhantom({
        machine_id: machineId ?? 'test',
        field_size_cm: 10,
        ssd_cm: 100,
      })
      expect(typeof result.max_dose_gy).toBe('number')
    })

    it('lockMachine 404 rejects for unknown machine', async () => {
      await expect(gw.lockMachine('nonexistent')).rejects.toThrow('API error 404')
    })
  })

  // ── Slices ────────────────────────────────────────────────────────────────

  describe('slices', () => {
    it('getSlice 404 rejects for unknown dataset', async () => {
      await expect(
        gw.getSlice({ datasetId: 'nonexistent', axis: 'axial', index: 0 })
      ).rejects.toThrow('Slice fetch failed: 404')
    })

    // Full happy-path slice test requires a mounted real DICOM dataset.
    // Run manually: mount a dataset, capture its id, then:
    //
    //   const slice = await gw.getSlice({ datasetId: '<id>', axis: 'axial', index: 60 })
    //   expect(slice.data.byteLength).toBeGreaterThan(0)
    //   expect(slice.width).toBeGreaterThan(0)
    //   expect(slice.height).toBeGreaterThan(0)
  })

  // ── Adaptive ──────────────────────────────────────────────────────────────

  describe('adaptive', () => {
    it('listRegistrationResults returns an array', async () => {
      const results = await gw.listRegistrationResults(createdSessionId)
      expect(Array.isArray(results)).toBe(true)
    })

    it('updateContourStatus 404 rejects for unknown contour', async () => {
      await expect(
        gw.updateContourStatus('nonexistent', 'accepted')
      ).rejects.toThrow()
    })

    it('getDoseAccumulation 404 rejects for session with no accumulation', async () => {
      await expect(
        gw.getDoseAccumulation(createdSessionId)
      ).rejects.toThrow()
    })
  })
})
```

---

## Seed script (if POST /patients is not implemented)

The sidecar has no `POST /patients` REST endpoint (patients come from the DICOM
scanner via `pycdms.scan_folder`). For integration tests you need a patient row.
Use this one-shot Python script to insert one:

```python
# scripts/seed_test_data.py  (run from AxiomaSideCar with venv active)
import asyncio, aiosqlite, uuid
from datetime import UTC, datetime
from database import DB_PATH

async def main():
    async with aiosqlite.connect(DB_PATH) as db:
        pid = str(uuid.uuid4())
        now = datetime.now(UTC).isoformat()
        await db.execute(
            "INSERT INTO patients (id, mrn, name, date_of_birth, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?)",
            (pid, "TEST-001", "Integration Test", "1980-01-01", now, now)
        )
        await db.commit()
        print(f"patient_id={pid}")

asyncio.run(main())
```

Then pass the printed `patient_id` into the test via an env var or hardcode it
in the `beforeAll`.

---

## Vitest config note

The existing `vite.config.ts` sets `environment: 'jsdom'` globally.
Integration tests need Node's real `fetch` (available in Node 18+).
Add a file-level override at the top of the test file if fetch is mocked:

```typescript
// @vitest-environment node
```

---

## CI note

These tests are **not** intended for GitHub Actions CI — the sidecar needs the
real engine repos which aren't available there. Keep them in a separate file
from the unit tests and exclude them in the workflow:

```yaml
# In AxiomaUX/.github/workflows/ci.yml (if it exists)
- name: Unit tests
  run: npx vitest run --exclude "**/*.integration.test.ts"
```

---

## Quick-reference commands

```bash
# Run only integration tests
VITE_API_BASE_URL=http://localhost:8000 VITE_USE_MOCK=false \
  npx vitest run src/gateway/LocalDataGateway.integration.test.ts

# Run with verbose output
VITE_API_BASE_URL=http://localhost:8000 VITE_USE_MOCK=false \
  npx vitest run --reporter=verbose src/gateway/LocalDataGateway.integration.test.ts

# Watch mode during development
VITE_API_BASE_URL=http://localhost:8000 VITE_USE_MOCK=false \
  npx vitest src/gateway/LocalDataGateway.integration.test.ts

# Sidecar with auto-reload (dev)
cd ~/repos/AxiomaSideCar && source .venv/bin/activate
uvicorn main:app --port 8000 --reload
```
