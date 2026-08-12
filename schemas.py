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


class DatasetItem(_Base):
    """One series (DICOM) or fraction/type-group (CDMS) within a dataset."""

    id: str
    dataset_id: str
    kind: str  # 'dicom_series' | 'cdms_group'
    # DICOM
    modality: str | None = None
    series_description: str | None = None
    series_instance_uid: str | None = None
    series_number: int | None = None
    frame_of_reference_uid: str | None = None
    sop_class_uid: str | None = None
    instance_count: int | None = None
    # CDMS
    type_code: str | None = None
    type_name: str | None = None
    fraction: str | None = None
    machine: str | None = None
    # shared
    source_path: str
    file_paths_json: str | None = None  # raw JSON string; parse client-side if needed
    item_count: int
    added_at: str


class DatasetMeta(_Base):
    """Study-level dataset container, with nested items."""

    id: str
    session_id: str
    label: str
    kind: str  # 'dicom' | 'cdms' | 'mixed'
    patient_name: str | None = None
    patient_id: str | None = None
    study_instance_uid: str | None = None
    frame_of_reference_uid: str | None = None
    content_type: str = ""
    path: str = ""  # legacy compat / slice endpoint
    created_at: str
    updated_at: str
    items: list[DatasetItem] = []


class VolumeMeta(_Base):
    dataset_id: str
    dimensions: list[int]
    spacing_mm: list[float]
    origin: list[float]
    direction: list[float]
    hu_min: int
    hu_max: int
    levels: list[int]


class Structure(_Base):
    id: str
    dataset_id: str
    roi_number: int
    name: str
    color: list[int] | None = None
    contour_count: int


class Contour(_Base):
    id: str
    structure_id: str
    slice_index: int | None = None
    geometric_type: str
    points: list[list[float]]


# ── Dataset mutation bodies ────────────────────────────────────────────────────


class DicomItemBody(_Base):
    """One DICOM series being added to a dataset."""

    kind: str = "dicom_series"
    modality: str | None = None
    series_description: str | None = None
    series_instance_uid: str | None = None
    series_number: int | None = None
    frame_of_reference_uid: str | None = None
    sop_class_uid: str | None = None
    instance_count: int | None = None
    source_path: str
    file_paths: list[str] = []
    # Study-level identity carried from scan results for auto-inference
    patient_name: str | None = None
    patient_id: str | None = None
    study_instance_uid: str | None = None


class CdmsItemBody(_Base):
    """One CDMS fraction/type-group being added to a dataset."""

    kind: str = "cdms_group"
    type_code: str | None = None
    type_name: str | None = None
    fraction: str | None = None
    machine: str | None = None
    source_path: str
    file_paths: list[str] = []
    item_count: int = 0


class CreateDatasetBody(_Base):
    label: str
    # Optional study-level identity; inferred from first DICOM item if absent
    patient_name: str | None = None
    patient_id: str | None = None
    study_instance_uid: str | None = None
    frame_of_reference_uid: str | None = None
    items: list[DicomItemBody | CdmsItemBody] = []


class AddItemsBody(_Base):
    items: list[DicomItemBody | CdmsItemBody]
    # Warn if study UID mismatches the dataset's study_instance_uid
    # (enforced server-side; set allow_mismatch=True to override)
    allow_mismatch: bool = False


# ── Jobs ───────────────────────────────────────────────────────────────────────


class JobStatus(_Base):
    id: str
    session_id: str
    type: str
    status: str
    progress: float
    message: str | None = None
    params: dict[str, Any] | None = None
    result: dict[str, Any] | list[Any] | None = None
    created_at: str
    updated_at: str


class JobArtifact(_Base):
    id: str
    job_id: str
    name: str
    path: str
    media_type: str | None = None
    size_bytes: int | None = None
    created_at: str


class Fraction(_Base):
    index: int
    label: str
    session_id: str
    dataset_item_id: str | None = None
    machine: str | None = None


class RegistrationSummary(_Base):
    id: str
    session_id: str
    job_id: str
    status: str
    result: dict[str, Any] | list[Any] | None = None
    metrics: list[RegistrationMetric] = []


class RegistrationMetric(_Base):
    fraction_index: int = Field(validation_alias="fractionIndex")
    rms_surface_distance_mm: float = Field(validation_alias="rmsSurfaceDistanceMm")
    mean_dice: float = Field(validation_alias="meanDice")
    approved: bool = False


class ContourReview(_Base):
    id: str
    session_id: str
    fraction_index: int
    structure_id: str
    status: str


class ProvenanceManifest(_Base):
    schema_version: int = Field(validation_alias="schemaVersion")
    sidecar_version: str = Field(validation_alias="sidecarVersion")
    session_id: str = Field(validation_alias="sessionId")
    git_sha: str | None = Field(default=None, validation_alias="gitSha")
    engine: dict[str, Any] = Field(default_factory=dict)


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


class BeamModel(_Base):
    machine_id: str
    version: str
    sha256: str
    parameters: dict[str, Any] = Field(default_factory=dict)


class GoldenData(_Base):
    machine_id: str
    version: str | None = None
    sha256: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)


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
    provenance: ProvenanceManifest | dict[str, Any] | None = None


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
