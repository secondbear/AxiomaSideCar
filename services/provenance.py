"""Versioned provenance manifests for SideCar engine results."""

import os
from importlib.metadata import PackageNotFoundError, version
from typing import Any


def _sidecar_version() -> str:
    try:
        return version("axioma-sidecar")
    except PackageNotFoundError:
        return "0.1.0"


def normalize_provenance(
    session_id: str, engine_provenance: dict[str, Any] | None
) -> dict[str, Any]:
    """Wrap engine provenance without fabricating unavailable identity fields."""
    return {
        "schemaVersion": 1,
        "sidecarVersion": _sidecar_version(),
        "sessionId": session_id,
        "gitSha": os.environ.get("AXIOMA_GIT_SHA"),
        "engine": engine_provenance or {},
    }
