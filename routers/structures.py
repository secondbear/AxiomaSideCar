from fastapi import APIRouter

from schemas import Contour, Structure
from services.structure_service import list_contours, list_structures

router = APIRouter()


@router.get("/datasets/{dataset_id}/structures", response_model=list[Structure])
async def get_structures(dataset_id: str):
    return await list_structures(dataset_id)


@router.get("/structures/{structure_id}/contours", response_model=list[Contour])
async def get_contours(structure_id: str, slice: int | None = None):
    dataset_id = structure_id.rsplit(":", 1)[0] if ":" in structure_id else ""
    return await list_contours(dataset_id, structure_id, slice)
