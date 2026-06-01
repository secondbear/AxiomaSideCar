"""Pydantic v2 response schemas for all sidecar routes.

These models serve two purposes:
  1. ``response_model=`` on routers → typed OpenAPI at /docs
  2. Runtime validation of what the service layer returns

Naming convention:
  - Fields use snake_case to match SQLite column names directly.
  - Engine-result schemas (DoseResult, AccumulatedDoseResult) use
    ``validation_alias`` for the camelCase keys the service layer returns.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class _Base(BaseModel):
    """Shared config: accept row-like objects and plain dicts interchangeably."""

    model_config = ConfigDict(from_attributes=True)


# ── Patient / Session / Dataset ────────────────────────────────────────────────


class Patient(_Base):
    id: str
    external_id: str | None = None
    name: str | None = None
    dob: str | None = None
    created_at: str


class Session(_Base):
    id: str
    patient_id: str
    label: str
    created_at: str
    updated_at: str


class DatasetMeta(_Base):
    id: str
    session_id: str
    path: str
    content_type: str
    file_count: int | None = None
    created_at: str


# ── Jobs ───────────────────────────────────────────────────────────────────────


class JobStatus(_Base):
    id: str
    session_id: str
    type: str
    status: str
    progress: float
    message: str | None = None
    params: str | None = None  # JSON blob
    result: str | None = None  # JSON blob, populated on completion
    created_at: str
    updated_at: str


# ── Commissioning ──────────────────────────────────────────────────────────────


class MachineRecord(_Base):
    id: str
    name: str
    engine: str
    status: str
    params: Any  # stored as JSON string in DB; dict on creation
    locked_hash: str | None = None
    created_at: str
    updated_at: str | None = None  # absent in the POST create response


# ── Engine results ─────────────────────────────────────────────────────────────


class DoseResult(BaseModel):
    """Returned by ``dose_service.run_dose_calc`` and ``run_phantom_calc``.

    The service layer uses camelCase keys; ``validation_alias`` lets Pydantic
    accept those keys while keeping Pythonic field names here.
    """

    model_config = ConfigDict(populate_by_name=True)

    max_dose_gy: float = Field(validation_alias="maxDoseGy")
    mean_dose_gy: float = Field(validation_alias="meanDoseGy")
    n_projections: int | None = Field(default=None, validation_alias="nProjections")
    provenance: dict[str, Any] | None = None


class RegistrationResult(BaseModel):
    """Returned by ``deform_service.run_registration``."""

    manifest: dict[str, Any]
    out_dir: str


class AccumulatedDoseResult(BaseModel):
    """Returned by ``deform_service.run_dose_accumulation`` (``report.as_dict()``).

    The exact keys depend on the GenDoseCalc version, so ``extra="allow"``
    passes unknown fields through unchanged.
    """

    model_config = ConfigDict(extra="allow")

    n_states: int | None = Field(default=None, validation_alias="nStates")
    total_weight: float | None = Field(default=None, validation_alias="totalWeight")
    accumulated_max_gy: float | None = Field(default=None, validation_alias="accumulatedMaxGy")
    accumulated_mean_gy: float | None = Field(default=None, validation_alias="accumulatedMeanGy")
    total_elapsed_s: float | None = Field(default=None, validation_alias="totalElapsedS")
