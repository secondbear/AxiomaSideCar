"""DICOM de-identification for research data export."""

import hashlib
import json
from pathlib import Path

import pydicom
from pydicom.uid import generate_uid

_IDENTIFIER_TAGS = (
    "PatientName",
    "PatientID",
    "PatientBirthDate",
    "PatientSex",
    "OtherPatientIDs",
    "OtherPatientNames",
    "PatientAddress",
    "PatientTelephoneNumbers",
    "InstitutionName",
    "InstitutionAddress",
    "ReferringPhysicianName",
    "PerformingPhysicianName",
    "OperatorsName",
    "AccessionNumber",
)


def _stable_patient_id(value: str) -> str:
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:16].upper()
    return f"ANON-{digest}"


def deidentify_dicom_tree(source_dir: str, output_dir: str) -> dict:
    source = Path(source_dir).resolve()
    output = Path(output_dir).resolve()
    if not source.is_dir():
        raise ValueError(f"Source directory not found: {source_dir}")
    if source == output or source in output.parents:
        raise ValueError("Output directory must not be inside the source directory")

    output.mkdir(parents=True, exist_ok=True)
    files = sorted(path for path in source.rglob("*") if path.is_file())
    if not files:
        raise ValueError("Source directory contains no files")

    manifest = []
    patient_map: dict[str, str] = {}
    for input_path in files:
        relative = input_path.relative_to(source)
        output_path = output / relative
        output_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            dataset = pydicom.dcmread(input_path)
        except (OSError, pydicom.errors.InvalidDicomError):
            continue

        original_patient_id = str(getattr(dataset, "PatientID", "UNKNOWN"))
        anonymized_id = patient_map.setdefault(
            original_patient_id, _stable_patient_id(original_patient_id)
        )
        for attribute in _IDENTIFIER_TAGS:
            if hasattr(dataset, attribute):
                delattr(dataset, attribute)
        dataset.PatientID = anonymized_id
        dataset.PatientName = "ANONYMOUS"
        dataset.StudyInstanceUID = generate_uid()
        dataset.SeriesInstanceUID = generate_uid()
        if hasattr(dataset, "SOPInstanceUID"):
            dataset.SOPInstanceUID = generate_uid()
        dataset.save_as(output_path)
        manifest.append({"path": str(relative), "size_bytes": output_path.stat().st_size})

    if not manifest:
        raise ValueError("Source directory contains no readable DICOM files")

    manifest_path = output / "deidentification-manifest.json"
    manifest_path.write_text(
        json.dumps({"files": manifest, "file_count": len(manifest)}, indent=2) + "\n"
    )
    return {
        "file_count": len(manifest),
        "output_dir": str(output),
        "artifacts": [str(manifest_path), *(str(output / item["path"]) for item in manifest)],
    }
