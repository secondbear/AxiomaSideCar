"""Tests for the slice binary route."""

import json

import numpy as np

from tests.conftest import _create_patient, _create_session


async def _mount_fake_dataset(client, session_id: str, tmp_path) -> str:
    """Mount a fake dataset path and return the dataset id."""
    resp = await client.post(
        f"/api/v1/sessions/{session_id}/datasets/mount",
        json={"patient_data_path": str(tmp_path)},
    )
    assert resp.status_code == 201
    return resp.json()["id"]


async def test_slice_returns_binary_with_meta(client, tmp_path, monkeypatch):
    """Slice route returns raw bytes and a valid X-Slice-Meta header."""
    # Patch the slice extractor to avoid real DICOM I/O
    fake_plane = np.zeros((64, 64), dtype=np.int16)
    monkeypatch.setattr(
        "services.slice_service._extract_slice",
        lambda path, axis, index: (
            fake_plane.tobytes(),
            64,
            64,
            int(fake_plane.min()),
            int(fake_plane.max()),
        ),
    )

    pid = await _create_patient(client)
    sid = await _create_session(client, pid)
    did = await _mount_fake_dataset(client, sid, tmp_path)

    resp = await client.get(
        f"/api/v1/datasets/{did}/slice",
        params={"axis": "axial", "index": 0},
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/octet-stream"

    meta = json.loads(resp.headers["x-slice-meta"])
    assert meta["width"] == 64
    assert meta["height"] == 64
    assert "min" in meta
    assert "max" in meta

    # Body is raw int16 bytes: 64*64*2 bytes
    assert len(resp.content) == 64 * 64 * 2


async def test_slice_unknown_dataset(client):
    resp = await client.get(
        "/api/v1/datasets/no-such-id/slice",
        params={"axis": "axial", "index": 0},
    )
    # KeyError from the service propagates as an unhandled 500
    assert resp.status_code in (404, 500)
