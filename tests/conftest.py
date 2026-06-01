"""Shared fixtures for the AxiomaSideCar test suite.

All engine calls (GenDoseCalc, DeformCTMovement, pycdms) are monkeypatched so
tests run with zero patient data and no GPU.

Database
--------
Each test function gets a fresh in-memory SQLite database via the ``db_path``
fixture.  ``config.settings.db_path`` is patched to ``":memory:"`` before the
FastAPI app is imported, and the app's ``lifespan`` (which calls ``init_db``) is
run through the async test client.

TestClient
----------
``client`` is an ``httpx.AsyncClient`` backed by the FastAPI ASGI app.  Use it
directly in tests with ``await client.get(...)``.
"""

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

# ── Patch config before importing the app ─────────────────────────────────────


@pytest.fixture(autouse=True)
def _use_memory_db(monkeypatch, tmp_path):
    """Point the whole app at a fresh per-test SQLite file.

    ':memory:' creates a NEW database on every aiosqlite.connect() call so
    tables created in init_db() are invisible to subsequent connections.
    A real temp file shares state across all connections in the same test.

    Every module that did ``from database import DB_PATH`` gets its own copy
    of the name, so we must patch each one individually — patching only
    ``database.DB_PATH`` wouldn't affect the already-imported copies.
    """
    db_file = str(tmp_path / "test.db")

    import config
    import database
    import jobs.worker
    import routers.adaptive
    import routers.commissioning
    import services.session_service
    import services.slice_service

    monkeypatch.setattr(config.settings, "db_path", db_file)
    monkeypatch.setattr(database, "DB_PATH", db_file)
    monkeypatch.setattr(services.session_service, "DB_PATH", db_file)
    monkeypatch.setattr(services.slice_service, "DB_PATH", db_file)
    monkeypatch.setattr(routers.commissioning, "DB_PATH", db_file)
    monkeypatch.setattr(routers.adaptive, "DB_PATH", db_file)
    monkeypatch.setattr(jobs.worker, "DB_PATH", db_file)


# ── Engine stubs ───────────────────────────────────────────────────────────────


FAKE_DOSE = {
    "maxDoseGy": 42.0,
    "meanDoseGy": 21.0,
    "nProjections": 100,
    "provenance": {"machine": "test", "beam_model": "stub"},
}

FAKE_REGISTRATION = {
    "manifest": {"n_states": 5, "fractions": []},
    "out_dir": "/tmp/fake_out",
}

FAKE_ACCUMULATION = {
    "nStates": 5,
    "totalWeight": 1.0,
    "accumulatedMaxGy": 40.0,
    "accumulatedMeanGy": 20.0,
    "totalElapsedS": 0.1,
}

FAKE_DVH = {
    "CTV": {
        "structureName": "CTV",
        "d95Gy": 38.0,
        "d50Gy": 40.0,
        "d2Gy": 42.0,
        "dmeanGy": 39.5,
        "volumeCc": 120.0,
    }
}

FAKE_GAMMA = {
    "passRatePct": 98.5,
    "maxGamma": 1.2,
    "doseThresholdPct": 10.0,
    "distanceThresholdMm": 1.0,
    "localGamma": False,
}


@pytest.fixture(autouse=True)
def _patch_engines(monkeypatch):
    """Replace all engine calls with synchronous stubs."""
    monkeypatch.setattr("services.dose_service.run_dose_calc", lambda sid, p: FAKE_DOSE)
    monkeypatch.setattr("services.dose_service.run_phantom_calc", lambda p: FAKE_DOSE)
    monkeypatch.setattr(
        "services.deform_service.run_registration", lambda sid, p: FAKE_REGISTRATION
    )
    monkeypatch.setattr(
        "services.deform_service.run_dose_accumulation", lambda sid, p: FAKE_ACCUMULATION
    )
    monkeypatch.setattr("jobs.handlers._run_dvh_calc", lambda p: FAKE_DVH)
    monkeypatch.setattr("jobs.handlers._run_gamma_calc", lambda p: FAKE_GAMMA)
    # pycdms.scan_folder used in mount_dataset; returns list of CdmsFile
    # objects; mount_dataset reads f.content.kind on each item
    monkeypatch.setattr(
        "services.session_service.scan_folder",
        lambda path: [
            type("F", (), {"content": type("C", (), {"kind": "ct_series"})()})(),
        ],
    )


# ── Async HTTP client ──────────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def client():
    """Async TestClient backed by the FastAPI app with in-memory DB."""
    from database import init_db
    from main import app

    # Re-run init_db so the in-memory DB has all tables
    await init_db()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


# ── Helpers ────────────────────────────────────────────────────────────────────


async def _create_patient(client) -> str:
    """Insert a patient row directly via the SQLite API and return its id."""
    import uuid
    from datetime import UTC, datetime

    import aiosqlite

    import database

    pid = str(uuid.uuid4())
    now = datetime.now(UTC).isoformat()
    async with aiosqlite.connect(database.DB_PATH) as db:
        await db.execute(
            "INSERT INTO patients (id, external_id, name, dob, created_at) VALUES (?,?,?,?,?)",
            (pid, "MRN-001", "Test Patient", "1990-01-01", now),
        )
        await db.commit()
    return pid


async def _create_session(client, patient_id: str) -> str:
    resp = await client.post(
        "/api/v1/sessions", json={"patient_id": patient_id, "label": "Fraction 1"}
    )
    assert resp.status_code == 201
    return resp.json()["id"]
