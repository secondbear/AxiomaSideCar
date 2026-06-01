"""Slice service — extract raw int16 pixel planes from a DICOM CT series.

The dataset path (stored in SQLite by mount_dataset) points to a folder
containing a DICOM CT series.  pydicom loads the slices; the volume is
assembled in-memory as (Z, Y, X) int16 and the requested plane is returned
as raw bytes with its dimensions and window range.
"""
import asyncio
from pathlib import Path

import aiosqlite
import numpy as np
import pydicom

from database import DB_PATH


# ── Volume loader (CPU-bound, called via run_in_executor) ─────────────────────

def _load_ct_volume(dataset_path: str) -> np.ndarray:
    """Load a DICOM CT series into a (Z, Y, X) int16 numpy array.

    Slices are sorted by ImagePositionPatient[2] (z-axis) before stacking.
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

    slices.sort(key=lambda ds: float(ds.ImagePositionPatient[2]))
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


def _extract_slice(
    dataset_path: str, axis: str, index: int
) -> tuple[bytes, int, int, int, int]:
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
        int(plane.shape[1]),   # width
        int(plane.shape[0]),   # height
        int(plane.min()),
        int(plane.max()),
    )


# ── Public async API ──────────────────────────────────────────────────────────

async def get_slice_bytes(
    dataset_id: str, axis: str, index: int, lod: str
) -> tuple[bytes, int, int, int, int]:
    # Look up the dataset path from the database
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT path FROM datasets WHERE id=?", (dataset_id,)
        ) as cur:
            row = await cur.fetchone()

    if row is None:
        raise KeyError(f"Dataset {dataset_id!r} not found")

    dataset_path = row[0]
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _extract_slice, dataset_path, axis, index)

