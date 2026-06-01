import asyncio

import numpy as np
from pycdms import DataCatalogue  # adjust to actual pycdms public API

_catalogue = DataCatalogue()


async def get_slice_bytes(
    dataset_id: str, axis: str, index: int, lod: str
) -> tuple[bytes, int, int, int, int]:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _extract_slice, dataset_id, axis, index)


def _extract_slice(
    dataset_id: str, axis: str, index: int
) -> tuple[bytes, int, int, int, int]:
    vol = _catalogue.load_volume(dataset_id)  # numpy array (Z, Y, X)
    if axis == "axial":
        plane = vol[index, :, :]
    elif axis == "coronal":
        plane = vol[:, index, :]
    else:  # sagittal
        plane = vol[:, :, index]
    plane = plane.astype(np.int16)
    return (
        plane.tobytes(),
        int(plane.shape[1]),  # width
        int(plane.shape[0]),  # height
        int(plane.min()),
        int(plane.max()),
    )
