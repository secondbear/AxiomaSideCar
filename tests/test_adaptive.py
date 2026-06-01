"""Tests for the adaptive router — contour review PATCH status."""

import aiosqlite

import routers.adaptive as adaptive_module


async def _seed_contour(contour_id: str) -> None:
    """Insert a minimal contour_reviews row so PATCH can find it.

    Uses the module-level DB_PATH from routers.adaptive, which is already
    patched to the per-test temp file by the _use_memory_db conftest fixture.
    """
    async with aiosqlite.connect(adaptive_module.DB_PATH) as db:
        await db.execute(
            "INSERT OR IGNORE INTO contour_reviews "
            "(id, session_id, fraction_index, structure_id, status) VALUES (?,?,?,?,?)",
            (contour_id, "", 0, "", "pending"),
        )
        await db.commit()


async def test_patch_contour_status_accepted(client):
    await _seed_contour("contour-abc")
    resp = await client.patch(
        "/api/v1/adaptive/contours/contour-abc/status",
        json={"status": "accepted"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == "contour-abc"
    assert body["status"] == "accepted"


async def test_patch_contour_status_rejected(client):
    await _seed_contour("contour-xyz")
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
    await _seed_contour("c2")
    await client.patch("/api/v1/adaptive/contours/c2/status", json={"status": "pending"})
    resp = await client.patch("/api/v1/adaptive/contours/c2/status", json={"status": "accepted"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "accepted"


async def test_patch_contour_unknown_returns_404(client):
    """PATCH on a contour that was never seeded must return 404."""
    resp = await client.patch(
        "/api/v1/adaptive/contours/does-not-exist/status",
        json={"status": "accepted"},
    )
    assert resp.status_code == 404
