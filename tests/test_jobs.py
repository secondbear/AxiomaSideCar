"""Tests for the jobs queue — enqueue, poll, lifecycle, and all handler types."""

import asyncio
import json

import pytest

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


async def test_job_completes(client):
    pid = await _create_patient(client)
    sid = await _create_session(client, pid)
    job = await _enqueue(client, sid, "dose-calc")
    final = await _wait_completed(client, job["id"])
    assert final["status"] == "completed"
    result = json.loads(final["result"])
    assert result["maxDoseGy"] == 42.0


async def test_job_failed_unknown_type(client):
    pid = await _create_patient(client)
    sid = await _create_session(client, pid)
    job = await _enqueue(client, sid, "unknown-type")
    final = await _wait_completed(client, job["id"])
    assert final["status"] == "failed"
    assert "Unknown job type" in final["message"]


async def test_list_jobs_for_session(client):
    pid = await _create_patient(client)
    sid = await _create_session(client, pid)
    j1 = await _enqueue(client, sid, "dose-calc")
    j2 = await _enqueue(client, sid, "phantom-calc")

    resp = await client.get(f"/api/v1/sessions/{sid}/jobs")
    assert resp.status_code == 200
    ids = [j["id"] for j in resp.json()]
    assert j1["id"] in ids
    assert j2["id"] in ids


async def test_dvh_calc_job(client):
    pid = await _create_patient(client)
    sid = await _create_session(client, pid)
    job = await _enqueue(client, sid, "dvh-calc", {"rtstruct_path": "/rs"})
    final = await _wait_completed(client, job["id"])
    assert final["status"] == "completed"
    result = json.loads(final["result"])
    assert "CTV" in result
    assert result["CTV"]["d95Gy"] == 38.0


async def test_gamma_calc_job(client):
    pid = await _create_patient(client)
    sid = await _create_session(client, pid)
    job = await _enqueue(client, sid, "gamma-calc")
    final = await _wait_completed(client, job["id"])
    assert final["status"] == "completed"
    result = json.loads(final["result"])
    assert result["passRatePct"] == 98.5
