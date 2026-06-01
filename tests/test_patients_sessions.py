"""Tests for patient / session / dataset routes."""

from tests.conftest import _create_patient, _create_session


async def test_list_patients_empty(client):
    resp = await client.get("/api/v1/patients")
    assert resp.status_code == 200
    assert resp.json() == []


async def test_get_patient_not_found(client):
    resp = await client.get("/api/v1/patients/does-not-exist")
    assert resp.status_code == 404


async def test_create_and_list_session(client):
    pid = await _create_patient(client)

    # Create session
    resp = await client.post("/api/v1/sessions", json={"patient_id": pid, "label": "Fx 1"})
    assert resp.status_code == 201
    body = resp.json()
    assert body["patient_id"] == pid
    assert body["label"] == "Fx 1"
    session_id = body["id"]

    # List sessions for patient
    resp = await client.get("/api/v1/sessions", params={"patient_id": pid})
    assert resp.status_code == 200
    ids = [s["id"] for s in resp.json()]
    assert session_id in ids


async def test_get_session_not_found(client):
    resp = await client.get("/api/v1/sessions/no-such-session")
    assert resp.status_code == 404


async def test_list_datasets_empty(client):
    pid = await _create_patient(client)
    sid = await _create_session(client, pid)
    resp = await client.get(f"/api/v1/sessions/{sid}/datasets")
    assert resp.status_code == 200
    assert resp.json() == []


async def test_mount_dataset(client, tmp_path):
    """mount_dataset writes a row to datasets; scan_folder is stubbed."""
    pid = await _create_patient(client)
    sid = await _create_session(client, pid)

    resp = await client.post(
        f"/api/v1/sessions/{sid}/datasets/mount",
        json={"patient_data_path": str(tmp_path)},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["session_id"] == sid
    assert body["path"] == str(tmp_path)
    # content_type is derived from the stubbed scan_folder result
    assert body["content_type"] == "ct_series"


async def test_list_patients_returns_schema_fields(client):
    """Response must match the Patient schema (snake_case fields)."""
    pid = await _create_patient(client)
    resp = await client.get("/api/v1/patients")
    assert resp.status_code == 200
    patient = next(p for p in resp.json() if p["id"] == pid)
    assert "id" in patient
    assert "created_at" in patient
