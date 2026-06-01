"""Tests for commissioning machine CRUD and lock endpoint."""


async def test_list_machines_empty(client):
    resp = await client.get("/api/v1/commissioning/machines")
    assert resp.status_code == 200
    assert resp.json() == []


async def test_create_machine(client, monkeypatch):
    # Stub the GenDoseCalc YAML sync so it doesn't touch the filesystem
    monkeypatch.setattr(
        "routers.commissioning._sync_to_gendosecalc",
        lambda machine_id, machine_dict: None,
    )

    resp = await client.post(
        "/api/v1/commissioning/machines",
        json={
            "name": "TrueBeam-1",
            "engine": "varian",
            "params": {"mu": 1.0, "sigmaP": 0.5},
        },
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["name"] == "TrueBeam-1"
    assert body["status"] == "draft"
    assert "id" in body


async def test_create_and_lock_machine(client, monkeypatch):
    monkeypatch.setattr(
        "routers.commissioning._sync_to_gendosecalc",
        lambda machine_id, machine_dict: None,
    )

    # Create
    resp = await client.post(
        "/api/v1/commissioning/machines",
        json={"name": "Elekta-1", "engine": "elekta", "params": {"mu": 0.9}},
    )
    assert resp.status_code == 201
    machine_id = resp.json()["id"]

    # Lock
    resp = await client.post("/api/v1/commissioning/lock", json={"machine_id": machine_id})
    assert resp.status_code == 200
    body = resp.json()
    assert body["machine_id"] == machine_id
    assert "locked_hash" in body
    assert len(body["locked_hash"]) == 64  # sha256 hex digest


async def test_lock_nonexistent_machine(client):
    resp = await client.post("/api/v1/commissioning/lock", json={"machine_id": "no-such-id"})
    assert resp.status_code == 404


async def test_upload_measurement(client):
    resp = await client.post(
        "/api/v1/commissioning/upload",
        files={"file": ("pdd.csv", b"depth,dose\n0,100\n50,80\n", "text/csv")},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["filename"] == "pdd.csv"
    assert body["size"] > 0
    assert body["status"] == "received"


async def test_water_phantom_calc(client, monkeypatch):
    monkeypatch.setattr(
        "routers.commissioning._sync_to_gendosecalc",
        lambda machine_id, machine_dict: None,
    )
    # Create machine first so lock endpoint works
    create_resp = await client.post(
        "/api/v1/commissioning/machines",
        json={"name": "TrueBeam-2", "engine": "varian", "params": {}},
    )
    machine_id = create_resp.json()["id"]

    resp = await client.post(
        "/api/v1/commissioning/calculate_water_phantom",
        json={
            "machine_id": machine_id,
            "engine": "varian",
            "parameters": {"field_size_cm": 10},
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    # DoseResult schema serialises to snake_case
    assert body["max_dose_gy"] == 42.0
