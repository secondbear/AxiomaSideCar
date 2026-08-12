"""Tests for the jobs queue — enqueue, poll, lifecycle, and all handler types."""

import asyncio
import json
import uuid
from datetime import UTC, datetime

import aiosqlite
import pytest

import jobs.worker as worker
from tests.conftest import _create_patient, _create_session


async def _enqueue(client, session_id: str, job_type: str, params: dict | None = None) -> dict:
    resp = await client.post(
        f"/api/v1/sessions/{session_id}/jobs",
        json={"type": job_type, "params": params or {}},
    )
    assert resp.status_code == 202
    return resp.json()


async def _wait_completed(client, job_id: str, timeout: float = 2.0) -> dict:
    """Poll until the job leaves 'queued'/'running' or timeout expires."""
    deadline = asyncio.get_event_loop().time() + timeout
    while True:
        resp = await client.get(f"/api/v1/jobs/{job_id}")
        assert resp.status_code == 200
        body = resp.json()
        if body["status"] not in ("queued", "running"):
            return body
        if asyncio.get_event_loop().time() > deadline:
            pytest.fail(f"Job {job_id} did not complete within {timeout}s: {body}")
        await asyncio.sleep(0.05)


async def test_enqueue_dose_calc(client):
    pid = await _create_patient(client)
    sid = await _create_session(client, pid)
    job = await _enqueue(client, sid, "dose-calc", {"rtplan_path": "/p", "ct_dir": "/ct"})
    assert job["type"] == "dose-calc"
    assert job["status"] in ("queued", "running", "completed")


async def test_poll_unknown_job(client):
    resp = await client.get("/api/v1/jobs/does-not-exist")
    assert resp.status_code == 404


async def test_cancel_unknown_job(client):
    resp = await client.delete("/api/v1/jobs/does-not-exist")
    assert resp.status_code == 404


async def test_list_job_artifacts_for_job(client):
    pid = await _create_patient(client)
    sid = await _create_session(client, pid)
    job = await _enqueue(client, sid, "dose-calc", {"rtplan_path": "/p", "ct_dir": "/ct"})

    resp = await client.get(f"/api/v1/jobs/{job['id']}/artifacts")

    assert resp.status_code == 200
    assert resp.json() == []


async def test_register_and_list_job_artifact(client, tmp_path):
    pid = await _create_patient(client)
    sid = await _create_session(client, pid)
    job = await _enqueue(client, sid, "dose-calc", {"rtplan_path": "/p", "ct_dir": "/ct"})
    artifact_path = tmp_path / "manifest.json"
    artifact_path.write_text("{}")

    await worker._register_result_artifacts(job["id"], {"artifacts": [str(artifact_path)]})
    response = await client.get(f"/api/v1/jobs/{job['id']}/artifacts")

    assert response.status_code == 200
    assert response.json()[0]["name"] == "manifest.json"
    assert response.json()[0]["size_bytes"] == 2


async def test_job_events_unknown_job(client):
    response = await client.get("/api/v1/jobs/missing/events")

    assert response.status_code == 404


async def test_job_validation_rejects_missing_params(client):
    pid = await _create_patient(client)
    sid = await _create_session(client, pid)

    response = await client.post(
        f"/api/v1/sessions/{sid}/jobs",
        json={"type": "dose-calc", "params": {}},
    )

    assert response.status_code == 422
    assert "rtplan_path" in response.json()["detail"]["missing"]


async def test_job_events_emit_terminal_state(client):
    pid = await _create_patient(client)
    sid = await _create_session(client, pid)
    job = await _enqueue(client, sid, "dose-calc", {"rtplan_path": "/p", "ct_dir": "/ct"})

    async with client.stream(
        "GET", f"/api/v1/jobs/{job['id']}/events", params={"interval_ms": 50}
    ) as response:
        body = await response.aread()

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert b"event: job" in body
    assert b'"status": "completed"' in body


async def test_job_completes(client):
    pid = await _create_patient(client)
    sid = await _create_session(client, pid)
    job = await _enqueue(client, sid, "dose-calc", {"rtplan_path": "/p", "ct_dir": "/ct"})
    final = await _wait_completed(client, job["id"])
    assert final["status"] == "completed"
    result = final["result"]
    assert result["maxDoseGy"] == 42.0


async def test_worker_recovers_running_job_after_restart(client):
    pid = await _create_patient(client)
    sid = await _create_session(client, pid)
    job_id = str(uuid.uuid4())
    now = datetime.now(UTC).isoformat()
    async with aiosqlite.connect(worker.DB_PATH) as db:
        await db.execute(
            "INSERT INTO jobs "
            "(id, session_id, type, status, progress, params, created_at, updated_at) "
            "VALUES (?, ?, 'dose-calc', 'running', 0.4, ?, ?, ?)",
            (job_id, sid, json.dumps({"rtplan_path": "/p", "ct_dir": "/ct"}), now, now),
        )
        await db.commit()

    await worker.stop_worker()
    await worker.start_worker()
    final = await _wait_completed(client, job_id)

    assert final["status"] == "completed"
    assert final["message"] is None


async def test_job_failed_unknown_type(client):
    pid = await _create_patient(client)
    sid = await _create_session(client, pid)
    response = await client.post(
        f"/api/v1/sessions/{sid}/jobs",
        json={"type": "unknown-type", "params": {}},
    )
    assert response.status_code == 422


async def test_list_jobs_for_session(client):
    pid = await _create_patient(client)
    sid = await _create_session(client, pid)
    j1 = await _enqueue(client, sid, "dose-calc", {"rtplan_path": "/p", "ct_dir": "/ct"})
    j2 = await _enqueue(
        client,
        sid,
        "phantom-calc",
        {"rtplan_path": "/p", "ct_dir": "/ct", "motion_path": "/motion"},
    )

    resp = await client.get(f"/api/v1/sessions/{sid}/jobs")
    assert resp.status_code == 200
    ids = [j["id"] for j in resp.json()]
    assert j1["id"] in ids
    assert j2["id"] in ids


async def test_dvh_calc_job(client):
    pid = await _create_patient(client)
    sid = await _create_session(client, pid)
    job = await _enqueue(
        client,
        sid,
        "dvh-calc",
        {"rtplan_path": "/p", "ct_dir": "/ct", "rtstruct_path": "/rs"},
    )
    final = await _wait_completed(client, job["id"])
    assert final["status"] == "completed"
    result = final["result"]
    assert "CTV" in result
    assert result["CTV"]["d95Gy"] == 38.0


async def test_gamma_calc_job(client):
    pid = await _create_patient(client)
    sid = await _create_session(client, pid)
    job = await _enqueue(
        client,
        sid,
        "gamma-calc",
        {
            "reference_rtplan_path": "/ref-plan",
            "reference_ct_dir": "/ref-ct",
            "evaluation_rtplan_path": "/eval-plan",
            "evaluation_ct_dir": "/eval-ct",
            "motion_path": "/motion",
        },
    )
    final = await _wait_completed(client, job["id"])
    assert final["status"] == "completed"
    result = final["result"]
    assert result["passRatePct"] == 98.5
