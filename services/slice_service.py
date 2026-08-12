"""Slice service — extract raw int16 pixel planes from a DICOM CT series.

The dataset path (stored in SQLite by mount_dataset) points to a folder
containing a DICOM CT series.  pydicom loads the slices; the volume is
assembled in-memory as (Z, Y, X) int16 and the requested plane is returned
as raw bytes with its dimensions and window range.
"""

import asyncio
from functools import lru_cache
from pathlib import Path

import aiosqlite
import numpy as np
import pydicom
import zarr

from database import DB_PATH

# ── Volume loader (CPU-bound, called via run_in_executor) ─────────────────────


def _orientation_vectors(dataset) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    values = [
        float(value) for value in getattr(dataset, "ImageOrientationPatient", [1, 0, 0, 0, 1, 0])
    ]
    row = np.asarray(values[:3], dtype=float)
    column = np.asarray(values[3:], dtype=float)
    normal = np.cross(row, column)
    return row, column, normal


def _slice_position(dataset, normal: np.ndarray) -> float:
    position = np.asarray(dataset.ImagePositionPatient, dtype=float)
    return float(np.dot(position, normal))


@lru_cache(maxsize=4)
def _load_ct_volume(dataset_path: str) -> np.ndarray:
    """Load a DICOM CT series into a (Z, Y, X) int16 numpy array.

    Slices are sorted by position along the DICOM orientation normal.
    """
    dcm_files = sorted(Path(dataset_path).glob("*.dcm"))
    if not dcm_files:
        raise FileNotFoundError(f"No DICOM files found in {dataset_path!r}")

    slices = []
    for f in dcm_files:
        ds = pydicom.dcmread(str(f), stop_before_pixels=False)
        if not hasattr(ds, "ImagePositionPatient"):
            continue
        slices.append(ds)

    if not slices:
        raise ValueError(f"No valid CT slices in {dataset_path!r}")

    _, _, normal = _orientation_vectors(slices[0])
    slices.sort(key=lambda ds: _slice_position(ds, normal))
    arrays = []
    for ds in slices:
        arr = ds.pixel_array.astype(np.int16)
        # Apply rescale slope/intercept to get HU values
        slope = float(getattr(ds, "RescaleSlope", 1))
        intercept = float(getattr(ds, "RescaleIntercept", 0))
        if slope != 1.0 or intercept != 0.0:
            arr = (arr * slope + intercept).astype(np.int16)
        arrays.append(arr)

    return np.stack(arrays, axis=0)  # (Z, Y, X)


def _extract_slice(dataset_path: str, axis: str, index: int) -> tuple[bytes, int, int, int, int]:
    vol = _load_ct_volume(dataset_path)  # (Z, Y, X)

    if axis == "axial":
        plane = vol[index, :, :]
    elif axis == "coronal":
        plane = vol[:, index, :]
    elif axis == "sagittal":
        plane = vol[:, :, index]
    else:
        raise ValueError(f"axis must be axial|coronal|sagittal, got {axis!r}")

    plane = plane.astype(np.int16)
    return (
        plane.tobytes(),
        int(plane.shape[1]),  # width
        int(plane.shape[0]),  # height
        int(plane.min()),
        int(plane.max()),
    )


def _read_volume_metadata(dataset_path: str) -> dict:
    dcm_files = sorted(Path(dataset_path).glob("*.dcm"))
    headers = []
    for path in dcm_files:
        dataset = pydicom.dcmread(str(path), stop_before_pixels=True)
        if getattr(dataset, "Modality", "") == "CT" and hasattr(dataset, "ImagePositionPatient"):
            headers.append(dataset)
    if not headers:
        raise FileNotFoundError(f"No CT slices found in {dataset_path!r}")
    headers.sort(key=lambda dataset: float(dataset.ImagePositionPatient[2]))

    volume = _load_ct_volume(dataset_path)
    first = headers[0]
    row, column, normal = _orientation_vectors(first)
    headers.sort(key=lambda dataset: _slice_position(dataset, normal))
    positions = [_slice_position(dataset, normal) for dataset in headers]
    if len(positions) > 1:
        z_spacing = float(np.median(np.diff(positions)))
    else:
        z_spacing = float(getattr(first, "SliceThickness", 1.0))
    pixel_spacing = [float(value) for value in getattr(first, "PixelSpacing", [1.0, 1.0])]
    return {
        "dimensions": [int(volume.shape[2]), int(volume.shape[1]), int(volume.shape[0])],
        "spacing_mm": [pixel_spacing[1], pixel_spacing[0], abs(z_spacing)],
        "origin": [float(value) for value in first.ImagePositionPatient],
        "direction": [float(value) for value in (*row, *column, *normal)],
        "hu_min": int(volume.min()),
        "hu_max": int(volume.max()),
        "levels": [0],
    }


# ── Public async API ──────────────────────────────────────────────────────────


async def get_slice_bytes(
    dataset_id: str, axis: str, index: int, lod: str
) -> tuple[bytes, int, int, int, int]:
    # Look up the dataset path from the database
    async with (
        aiosqlite.connect(DB_PATH) as db,
        db.execute("SELECT path FROM datasets WHERE id=?", (dataset_id,)) as cur,
    ):
        row = await cur.fetchone()

    if row is None:
        raise KeyError(f"Dataset {dataset_id!r} not found")

    dataset_path = row[0]
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _extract_slice, dataset_path, axis, index)


async def get_volume_metadata(dataset_id: str) -> dict:
    async with (
        aiosqlite.connect(DB_PATH) as db,
        db.execute("SELECT path FROM datasets WHERE id=?", (dataset_id,)) as cur,
    ):
        row = await cur.fetchone()
    if row is None:
        raise KeyError(f"Dataset {dataset_id!r} not found")
    loop = asyncio.get_event_loop()
    metadata = await loop.run_in_executor(None, _read_volume_metadata, row[0])
    dimensions = metadata["dimensions"]
    max_level = 0
    while max(dimensions) // (2 ** (max_level + 1)) >= 1:
        max_level += 1
    metadata["levels"] = list(range(max_level + 1))
    return {"dataset_id": dataset_id, **metadata}


def _volume_bytes(dataset_path: str, level: int) -> tuple[bytes, list[int]]:
    if level < 0:
        raise ValueError("level must be non-negative")
    pyramid = _open_volume_pyramid(dataset_path)
    available = sorted(int(name) for name in pyramid.array_keys() if name.isdigit())
    if level not in available:
        raise ValueError(f"level must be between 0 and {available[-1]}")
    sampled = pyramid[str(level)][:]
    dimensions = [int(sampled.shape[2]), int(sampled.shape[1]), int(sampled.shape[0])]
    return sampled.astype(np.int16, copy=False).tobytes(order="C"), dimensions


def _pyramid_signature(dataset_path: str) -> str:
    files = sorted(Path(dataset_path).glob("*.dcm"))
    return ":".join(
        f"{path.name}:{path.stat().st_size}:{path.stat().st_mtime_ns}" for path in files
    )


def _open_volume_pyramid(dataset_path: str):
    root = Path(dataset_path) / ".axioma-volume.zarr"
    signature = _pyramid_signature(dataset_path)
    pyramid = zarr.open_group(str(root), mode="a")
    if pyramid.attrs.get("source_signature") == signature and "0" in pyramid:
        return pyramid

    for name in list(pyramid.array_keys()):
        del pyramid[name]
    volume = _load_ct_volume(dataset_path)
    levels = []
    current = volume
    level = 0
    while True:
        pyramid.create_array(
            str(level),
            data=current,
            chunks=tuple(min(64, size) for size in current.shape),
            overwrite=True,
        )
        levels.append({"path": str(level)})
        if max(current.shape) <= 1:
            break
        current = current[::2, ::2, ::2]
        level += 1

    metadata = _read_volume_metadata(dataset_path)
    spacing = metadata["spacing_mm"]
    pyramid.attrs.update(
        {
            "source_signature": signature,
            "multiscales": [
                {
                    "version": "0.4",
                    "name": "ct",
                    "axes": [
                        {"name": "z", "type": "space", "unit": "millimeter"},
                        {"name": "y", "type": "space", "unit": "millimeter"},
                        {"name": "x", "type": "space", "unit": "millimeter"},
                    ],
                    "datasets": levels,
                    "metadata": {"spacingMm": [spacing[2], spacing[1], spacing[0]]},
                }
            ],
        }
    )
    return pyramid


async def get_volume_bytes(dataset_id: str, level: int) -> tuple[bytes, list[int]]:
    async with (
        aiosqlite.connect(DB_PATH) as db,
        db.execute("SELECT path FROM datasets WHERE id=?", (dataset_id,)) as cur,
    ):
        row = await cur.fetchone()
    if row is None:
        raise KeyError(f"Dataset {dataset_id!r} not found")
    loop = asyncio.get_event_loop()
    try:
        return await loop.run_in_executor(None, _volume_bytes, row[0], level)
    except ValueError:
        raise
