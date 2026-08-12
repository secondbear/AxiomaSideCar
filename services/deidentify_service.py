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


def _collect_uids(dataset: pydicom.Dataset, uid_map: dict[str, str]) -> None:
    for element in dataset.iterall():
        if element.VR != "UI" or not element.value:
            continue
        values = element.value if isinstance(element.value, list) else [element.value]
        for value in values:
            original = str(value)
            uid_map.setdefault(original, generate_uid())


def _remap_uids(dataset: pydicom.Dataset, uid_map: dict[str, str]) -> None:
    for element in dataset.iterall():
        if element.VR != "UI" or not element.value:
            continue
        if isinstance(element.value, list):
            element.value = [uid_map.get(str(value), str(value)) for value in element.value]
        else:
            element.value = uid_map.get(str(element.value), str(element.value))
    for element in dataset.file_meta.iterall():
        if element.VR == "UI" and element.value:
            element.value = uid_map.get(str(element.value), str(element.value))


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
    datasets: list[tuple[Path, Path, pydicom.Dataset]] = []
    for input_path in files:
        relative = input_path.relative_to(source)
        output_path = output / relative
        output_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            dataset = pydicom.dcmread(input_path)
        except (OSError, pydicom.errors.InvalidDicomError):
            continue
        datasets.append((relative, output_path, dataset))

    if not datasets:
        raise ValueError("Source directory contains no readable DICOM files")

    uid_map: dict[str, str] = {}
    for _, _, dataset in datasets:
        _collect_uids(dataset, uid_map)

    for relative, output_path, dataset in datasets:
        original_patient_id = str(getattr(dataset, "PatientID", "UNKNOWN"))
        anonymized_id = patient_map.setdefault(
            original_patient_id, _stable_patient_id(original_patient_id)
        )
        for attribute in _IDENTIFIER_TAGS:
            if hasattr(dataset, attribute):
                delattr(dataset, attribute)
        dataset.PatientID = anonymized_id
        dataset.PatientName = "ANONYMOUS"
        _remap_uids(dataset, uid_map)
        dataset.save_as(output_path)
        manifest.append({"path": str(relative), "size_bytes": output_path.stat().st_size})

    manifest_path = output / "deidentification-manifest.json"
    manifest_path.write_text(
        json.dumps({"files": manifest, "file_count": len(manifest)}, indent=2) + "\n"
    )
    return {
        "file_count": len(manifest),
        "output_dir": str(output),
        "artifacts": [str(manifest_path), *(str(output / item["path"]) for item in manifest)],
    }
