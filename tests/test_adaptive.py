"""Tests for the adaptive router — contour review PATCH status."""


async def test_patch_contour_status_accepted(client):
    resp = await client.patch(
        "/api/v1/adaptive/contours/contour-abc/status",
        json={"status": "accepted"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == "contour-abc"
    assert body["status"] == "accepted"


async def test_patch_contour_status_rejected(client):
    resp = await client.patch(
        "/api/v1/adaptive/contours/contour-xyz/status",
        json={"status": "rejected"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "rejected"


async def test_patch_contour_invalid_status(client):
    resp = await client.patch(
        "/api/v1/adaptive/contours/c1/status",
        json={"status": "approved"},  # not a valid value
    )
    assert resp.status_code == 422


async def test_patch_contour_idempotent(client):
    """Patching the same contour twice should update its status."""
    await client.patch("/api/v1/adaptive/contours/c2/status", json={"status": "pending"})
    resp = await client.patch("/api/v1/adaptive/contours/c2/status", json={"status": "accepted"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "accepted"
