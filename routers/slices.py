import json

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from services.slice_service import get_slice_bytes

router = APIRouter()


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
