"""Recursive DICOM directory scanner — reads tag headers only (no pixel data).

GET /api/v1/dicom/scan?path=<absolute_dir>&max_files=2000
Groups discovered .dcm files by StudyInstanceUID → SeriesInstanceUID and
returns a structured tree with all the UIDs and metadata fields used for
identity matching / series selection.
"""

import time
from pathlib import Path

import pydicom
import pydicom.errors
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/dicom")

# DICOM tags we want — read as keyword strings
_TAGS = [
    "PatientName",
    "PatientID",
    "StudyInstanceUID",
    "StudyDescription",
    "StudyDate",
    "StudyTime",
    "SeriesInstanceUID",
    "SeriesDescription",
    "SeriesNumber",
    "Modality",
    "FrameOfReferenceUID",
    "SOPInstanceUID",
    "SOPClassUID",
    "InstanceNumber",
]

# Stop reading pixels — we only want headers
_STOP_TAG = "PixelData"


def _safe_str(ds: pydicom.Dataset, tag: str, default: str | None = None) -> str | None:
    try:
        val = ds.get(tag)
        if val is None:
            return default
        # PersonName → str, all others already str-like
        return str(val).strip() or default
    except Exception:
        return default


def _safe_int(ds: pydicom.Dataset, tag: str) -> int | None:
    try:
        val = ds.get(tag)
        return int(val) if val is not None else None
    except Exception:
        return None


class DicomInstance(BaseModel):
    file_path: str
    sop_instance_uid: str | None
    sop_class_uid: str | None
    instance_number: int | None


class DicomSeries(BaseModel):
    series_instance_uid: str
    series_description: str | None
    series_number: int | None
    modality: str | None
    frame_of_reference_uid: str | None
    instance_count: int
    dir_path: str  # common parent directory for all instances in this series
    instances: list[DicomInstance]


class DicomStudy(BaseModel):
    study_instance_uid: str
    study_description: str | None
    study_date: str | None
    study_time: str | None
    patient_name: str | None
    patient_id: str | None
    series: list[DicomSeries]
    series_count: int
    total_instances: int


class ScanResult(BaseModel):
    root_path: str
    studies: list[DicomStudy]
    total_files_scanned: int
    dcm_files_found: int
    scan_time_ms: float
    truncated: bool


def _find_dcm_files(root: Path, max_files: int) -> tuple[list[Path], bool]:
    """Walk the tree and collect DICOM candidate files up to max_files."""
    found: list[Path] = []
    truncated = False
    for p in sorted(root.rglob("*")):
        # Accept .dcm or files with no suffix (common in DICOM exports)
        if p.is_file() and (p.suffix.lower() in (".dcm", "") or p.suffix.lower() == ".ima"):
            found.append(p)
            if len(found) >= max_files:
                truncated = True
                break
    return found, truncated


@router.get("/scan", response_model=ScanResult)
async def scan_directory(path: str, max_files: int = 2000):
    root = Path(path).resolve()
    if not root.exists():
        raise HTTPException(status_code=404, detail=f"Path not found: {path}")
    if not root.is_dir():
        raise HTTPException(status_code=422, detail=f"Not a directory: {path}")

    t0 = time.perf_counter()
    candidates, truncated = _find_dcm_files(root, max_files)

    # study_uid → { series_uid → { instances: [], meta: {} } }
    studies: dict[str, dict] = {}
    dcm_count = 0

    for fpath in candidates:
        try:
            ds = pydicom.dcmread(str(fpath), stop_before_pixels=True, force=False)
        except (pydicom.errors.InvalidDicomError, Exception):
            continue

        study_uid = _safe_str(ds, "StudyInstanceUID", "__unknown_study__")
        series_uid = _safe_str(ds, "SeriesInstanceUID", "__unknown_series__")
        dcm_count += 1

        if study_uid not in studies:
            studies[study_uid] = {
                "study_instance_uid": study_uid,
                "study_description": _safe_str(ds, "StudyDescription"),
                "study_date": _safe_str(ds, "StudyDate"),
                "study_time": _safe_str(ds, "StudyTime"),
                "patient_name": _safe_str(ds, "PatientName"),
                "patient_id": _safe_str(ds, "PatientID"),
                "series": {},
            }
        # Patch study-level patient info if we got a better value later
        if studies[study_uid]["patient_name"] is None:
            studies[study_uid]["patient_name"] = _safe_str(ds, "PatientName")
        if studies[study_uid]["patient_id"] is None:
            studies[study_uid]["patient_id"] = _safe_str(ds, "PatientID")

        series_map: dict = studies[study_uid]["series"]
        if series_uid not in series_map:
            series_map[series_uid] = {
                "series_instance_uid": series_uid,
                "series_description": _safe_str(ds, "SeriesDescription"),
                "series_number": _safe_int(ds, "SeriesNumber"),
                "modality": _safe_str(ds, "Modality"),
                "frame_of_reference_uid": _safe_str(ds, "FrameOfReferenceUID"),
                "instances": [],
                "_dirs": set(),
            }

        series_map[series_uid]["instances"].append(
            {
                "file_path": str(fpath),
                "sop_instance_uid": _safe_str(ds, "SOPInstanceUID"),
                "sop_class_uid": _safe_str(ds, "SOPClassUID"),
                "instance_number": _safe_int(ds, "InstanceNumber"),
            }
        )
        series_map[series_uid]["_dirs"].add(str(fpath.parent))

    # Build response models
    study_list: list[DicomStudy] = []
    for s_data in studies.values():
        series_list: list[DicomSeries] = []
        for sr_data in s_data["series"].values():
            dirs = sr_data.pop("_dirs")
            # Common parent: if all files are in the same dir use that; else use root
            if len(dirs) == 1:
                dir_path = next(iter(dirs))
            else:
                # Find the longest common prefix among dirs
                parts_list = [Path(d).parts for d in dirs]
                common = list(parts_list[0])
                for parts in parts_list[1:]:
                    for i, (a, b) in enumerate(zip(common, parts, strict=False)):
                        if a != b:
                            common = common[:i]
                            break
                    else:
                        common = common[: len(parts)]
                dir_path = str(Path(*common)) if common else str(root)

            # Sort instances by instance number
            sr_data["instances"].sort(key=lambda x: x["instance_number"] or 0)
            instance_count = len(sr_data["instances"])
            series_list.append(
                DicomSeries(
                    **{k: v for k, v in sr_data.items() if k != "instances"},
                    instance_count=instance_count,
                    dir_path=dir_path,
                    instances=[DicomInstance(**i) for i in sr_data["instances"]],
                )
            )

        series_list.sort(key=lambda s: (s.series_number or 9999, s.series_description or ""))
        total_instances = sum(s.instance_count for s in series_list)
        study_list.append(
            DicomStudy(
                study_instance_uid=s_data["study_instance_uid"],
                study_description=s_data["study_description"],
                study_date=s_data["study_date"],
                study_time=s_data["study_time"],
                patient_name=s_data["patient_name"],
                patient_id=s_data["patient_id"],
                series=series_list,
                series_count=len(series_list),
                total_instances=total_instances,
            )
        )

    elapsed_ms = (time.perf_counter() - t0) * 1000
    return ScanResult(
        root_path=str(root),
        studies=study_list,
        total_files_scanned=len(candidates),
        dcm_files_found=dcm_count,
        scan_time_ms=round(elapsed_ms, 1),
        truncated=truncated,
    )
