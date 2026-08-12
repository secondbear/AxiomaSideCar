"""RTSTRUCT metadata and contour extraction for dataset read APIs."""

import json
from pathlib import Path

import aiosqlite
import pydicom
from fastapi import HTTPException

from database import DB_PATH


def _candidate_files(source_path: str, file_paths_json: str | None) -> list[Path]:
    paths = []
    for raw_path in json.loads(file_paths_json or "[]"):
        paths.append(Path(raw_path))
    source = Path(source_path)
    if source.is_file():
        paths.append(source)
    elif source.is_dir():
        paths.extend(sorted(source.glob("*.dcm")))
    return list(dict.fromkeys(paths))


def _find_rtstruct(dataset_items: list[dict]) -> tuple[Path, object]:
    for item in dataset_items:
        candidates = _candidate_files(item["source_path"], item["file_paths_json"])
        for path in candidates:
            if not path.is_file():
                continue
            try:
                dataset = pydicom.dcmread(str(path), stop_before_pixels=True)
            except (OSError, ValueError, TypeError):
                continue
            if getattr(dataset, "Modality", "") == "RTSTRUCT":
                return path, dataset
    raise HTTPException(status_code=404, detail="No RTSTRUCT found in dataset")


async def _load_dataset_items(dataset_id: str) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM dataset_items WHERE dataset_id=? ORDER BY added_at",
            (dataset_id,),
        ) as cur:
            return [dict(row) for row in await cur.fetchall()]


def _read_ct_slice_positions(dataset_items: list[dict]) -> list[float]:
    positions = []
    for item in dataset_items:
        if item.get("modality") not in (None, "CT"):
            continue
        for path in _candidate_files(item["source_path"], item["file_paths_json"]):
            if not path.is_file():
                continue
            try:
                dataset = pydicom.dcmread(str(path), stop_before_pixels=True)
            except (OSError, ValueError, TypeError):
                continue
            image_position = getattr(dataset, "ImagePositionPatient", None)
            if getattr(dataset, "Modality", "") == "CT" and image_position:
                positions.append(float(image_position[2]))
    return sorted(set(positions))


def _slice_index(z: float, positions: list[float]) -> int | None:
    if not positions:
        return None
    nearest = min(range(len(positions)), key=lambda index: abs(positions[index] - z))
    if len(positions) == 1:
        tolerance = 1.0
    else:
        spacing = min(
            abs(positions[index] - positions[index - 1])
            for index in range(1, len(positions))
            if positions[index] != positions[index - 1]
        )
        tolerance = max(spacing / 2, 0.01)
    return nearest if abs(positions[nearest] - z) <= tolerance else None


def _parse_structures(dataset_id: str, dataset_items: list[dict]) -> list[dict]:
    _, rtstruct = _find_rtstruct(dataset_items)
    names = {
        int(roi.ROINumber): str(roi.ROIName)
        for roi in getattr(rtstruct, "StructureSetROISequence", [])
    }
    colors = {
        int(contour.ReferencedROINumber): [
            int(value) for value in getattr(contour, "ROIDisplayColor", [])
        ]
        for contour in getattr(rtstruct, "ROIContourSequence", [])
    }
    contour_counts = {
        int(contour.ReferencedROINumber): len(getattr(contour, "ContourSequence", []))
        for contour in getattr(rtstruct, "ROIContourSequence", [])
    }
    return [
        {
            "id": f"{dataset_id}:{roi_number}",
            "dataset_id": dataset_id,
            "roi_number": roi_number,
            "name": name,
            "color": colors.get(roi_number),
            "contour_count": contour_counts.get(roi_number, 0),
        }
        for roi_number, name in names.items()
    ]


def _parse_contours(
    dataset_id: str, structure_id: str, slice_index: int | None, dataset_items: list[dict]
) -> list[dict]:
    try:
        roi_number = int(structure_id.rsplit(":", 1)[1])
    except (IndexError, ValueError) as exc:
        raise HTTPException(status_code=404, detail="Structure not found") from exc
    if not structure_id.startswith(f"{dataset_id}:"):
        raise HTTPException(status_code=404, detail="Structure not found")

    _, rtstruct = _find_rtstruct(dataset_items)
    positions = _read_ct_slice_positions(dataset_items)
    results = []
    for roi_contour in getattr(rtstruct, "ROIContourSequence", []):
        if int(roi_contour.ReferencedROINumber) != roi_number:
            continue
        for contour_index, contour in enumerate(getattr(roi_contour, "ContourSequence", [])):
            data = [float(value) for value in contour.ContourData]
            points = [data[index : index + 3] for index in range(0, len(data), 3)]
            contour_slice = _slice_index(points[0][2], positions) if points else None
            if slice_index is not None and contour_slice != slice_index:
                continue
            results.append(
                {
                    "id": f"{structure_id}:{contour_index}",
                    "structure_id": structure_id,
                    "slice_index": contour_slice,
                    "geometric_type": str(
                        getattr(contour, "ContourGeometricType", "CLOSED_PLANAR")
                    ),
                    "points": points,
                }
            )
    if not results and not any(
        f"{dataset_id}:{roi_number}" == structure_id
        for structure in _parse_structures(dataset_id, dataset_items)
    ):
        raise HTTPException(status_code=404, detail="Structure not found")
    return results


async def list_structures(dataset_id: str) -> list[dict]:
    items = await _load_dataset_items(dataset_id)
    if not items:
        raise HTTPException(status_code=404, detail="Dataset not found")
    return _parse_structures(dataset_id, items)


async def list_contours(dataset_id: str, structure_id: str, slice_index: int | None) -> list[dict]:
    items = await _load_dataset_items(dataset_id)
    if not items:
        raise HTTPException(status_code=404, detail="Dataset not found")
    return _parse_contours(dataset_id, structure_id, slice_index, items)
