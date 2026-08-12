import json
import re

from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import Response

from schemas import VolumeMeta
from services.slice_service import get_slice_bytes, get_volume_bytes, get_volume_metadata

router = APIRouter()


@router.get("/datasets/{dataset_id}/meta", response_model=VolumeMeta)
async def get_volume_meta(dataset_id: str):
    try:
        return await get_volume_metadata(dataset_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/datasets/{dataset_id}/volume")
async def get_volume(
    dataset_id: str,
    level: int = 0,
    range_header: str | None = Header(default=None, alias="Range"),
):
    try:
        payload, dimensions = await get_volume_bytes(dataset_id, level)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    start, end = 0, len(payload) - 1
    status_code = 200
    if range_header:
        match = re.fullmatch(r"bytes=(\d+)-(\d*)", range_header.strip())
        if match is None:
            raise HTTPException(status_code=416, detail="Invalid byte range")
        start = int(match.group(1))
        end = int(match.group(2)) if match.group(2) else end
        if start > end or start >= len(payload):
            raise HTTPException(status_code=416, detail="Byte range not satisfiable")
        end = min(end, len(payload) - 1)
        status_code = 206

    body = payload[start : end + 1]
    return Response(
        content=body,
        status_code=status_code,
        media_type="application/octet-stream",
        headers={
            "Accept-Ranges": "bytes",
            "Content-Range": f"bytes {start}-{end}/{len(payload)}",
            "X-Volume-Meta": json.dumps(
                {"dimensions": dimensions, "dtype": "int16", "format": "ome-zarr", "level": level}
            ),
        },
    )


@router.get("/datasets/{dataset_id}/slice")
async def get_slice(
    dataset_id: str,
    axis: str,  # axial | coronal | sagittal
    index: int,
    lod: str = "native",
):
    try:
        buf, width, height, min_val, max_val = await get_slice_bytes(dataset_id, axis, index, lod)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    meta = json.dumps({"width": width, "height": height, "min": min_val, "max": max_val})
    return Response(
        content=buf,
        media_type="application/octet-stream",
        headers={"X-Slice-Meta": meta},
    )
