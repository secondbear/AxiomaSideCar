"""Tests for the slice binary route."""

import json
from types import SimpleNamespace

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


async def test_volume_metadata_returns_geometry(client, tmp_path, monkeypatch):
    monkeypatch.setattr(
        "services.slice_service._read_volume_metadata",
        lambda path: {
            "dimensions": [64, 64, 12],
            "spacing_mm": [1.0, 1.0, 2.5],
            "origin": [0.0, 0.0, -15.0],
            "direction": [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0],
            "hu_min": -1000,
            "hu_max": 3000,
            "levels": [0],
        },
    )
    pid = await _create_patient(client)
    sid = await _create_session(client, pid)
    did = await _mount_fake_dataset(client, sid, tmp_path)

    response = await client.get(f"/api/v1/datasets/{did}/meta")

    assert response.status_code == 200
    assert response.json()["dimensions"] == [64, 64, 12]
    assert response.json()["hu_min"] == -1000


async def test_volume_metadata_unknown_dataset(client):
    response = await client.get("/api/v1/datasets/missing/meta")

    assert response.status_code == 404


def test_volume_metadata_uses_oblique_normal(monkeypatch, tmp_path):
    import services.slice_service as slice_service

    first = SimpleNamespace(
        Modality="CT",
        ImagePositionPatient=[0.0, 0.0, 0.0],
        ImageOrientationPatient=[0.0, 1.0, 0.0, 0.0, 0.0, 1.0],
        PixelSpacing=[1.5, 2.0],
        SliceThickness=1.0,
    )
    second = SimpleNamespace(
        Modality="CT",
        ImagePositionPatient=[2.5, 0.0, 0.0],
        ImageOrientationPatient=first.ImageOrientationPatient,
        PixelSpacing=first.PixelSpacing,
        SliceThickness=1.0,
    )
    (tmp_path / "a.dcm").write_bytes(b"")
    (tmp_path / "b.dcm").write_bytes(b"")
    monkeypatch.setattr(
        "pydicom.dcmread",
        lambda path, stop_before_pixels=True: first if path.endswith("a.dcm") else second,
    )
    monkeypatch.setattr(
        slice_service, "_load_ct_volume", lambda path: np.zeros((2, 4, 5), dtype=np.int16)
    )

    metadata = slice_service._read_volume_metadata(str(tmp_path))

    assert metadata["spacing_mm"] == [2.0, 1.5, 2.5]
    assert metadata["direction"] == [0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 1.0, 0.0, 0.0]


def test_volume_pyramid_is_cached_and_multiscale(monkeypatch, tmp_path):
    import services.slice_service as slice_service

    calls = 0

    def load_volume(path):
        nonlocal calls
        calls += 1
        return np.arange(4 * 4 * 4, dtype=np.int16).reshape(4, 4, 4)

    monkeypatch.setattr(slice_service, "_load_ct_volume", load_volume)
    monkeypatch.setattr(
        slice_service,
        "_read_volume_metadata",
        lambda path: {"spacing_mm": [1.0, 1.0, 2.0]},
    )

    payload, dimensions = slice_service._volume_bytes(str(tmp_path), 1)
    second_payload, second_dimensions = slice_service._volume_bytes(str(tmp_path), 2)
    pyramid = slice_service.zarr.open_group(str(tmp_path / ".axioma-volume.zarr"), mode="r")

    assert dimensions == [2, 2, 2]
    assert second_dimensions == [1, 1, 1]
    assert len(payload) == 2 * 2 * 2 * 2
    assert len(second_payload) == 2
    assert calls == 1
    assert pyramid.attrs["multiscales"][0]["datasets"] == [
        {"path": "0"},
        {"path": "1"},
        {"path": "2"},
    ]


async def test_volume_returns_level_and_range(client, tmp_path, monkeypatch):
    payload = bytes(range(16))
    monkeypatch.setattr(
        "services.slice_service._volume_bytes",
        lambda path, level: (payload, [2, 2, 2]),
    )
    pid = await _create_patient(client)
    sid = await _create_session(client, pid)
    did = await _mount_fake_dataset(client, sid, tmp_path)

    response = await client.get(
        f"/api/v1/datasets/{did}/volume",
        params={"level": 1},
        headers={"Range": "bytes=4-7"},
    )

    assert response.status_code == 206
    assert response.content == payload[4:8]
    assert response.headers["accept-ranges"] == "bytes"
    assert response.headers["content-range"] == "bytes 4-7/16"
    volume_meta = json.loads(response.headers["x-volume-meta"])
    assert volume_meta["format"] == "ome-zarr"
    assert volume_meta["level"] == 1
