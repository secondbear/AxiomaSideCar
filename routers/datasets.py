from fastapi import APIRouter
from pydantic import BaseModel

from schemas import AddItemsBody, CreateDatasetBody, DatasetMeta
from services.privacy import sanitize_dataset
from services.session_service import (
    add_items_to_dataset,
    create_dataset,
    delete_dataset,
    get_datasets_for_session,
    mount_dataset,
    remove_dataset_item,
)

router = APIRouter()


# ── List ───────────────────────────────────────────────────────────────────────


@router.get("/sessions/{session_id}/datasets", response_model=list[DatasetMeta])
async def list_datasets(session_id: str):
    datasets = await get_datasets_for_session(session_id)
    return [sanitize_dataset(dataset) for dataset in datasets]


# ── Create ─────────────────────────────────────────────────────────────────────


@router.post("/sessions/{session_id}/datasets", status_code=201, response_model=DatasetMeta)
async def create(session_id: str, body: CreateDatasetBody):
    items = [i.model_dump() for i in body.items]
    dataset = await create_dataset(
        session_id=session_id,
        label=body.label,
        items=items,
        patient_name=body.patient_name,
        patient_id=body.patient_id,
        study_instance_uid=body.study_instance_uid,
        frame_of_reference_uid=body.frame_of_reference_uid,
    )
    return sanitize_dataset(dataset)


# ── Add items (incremental / multi-source) ─────────────────────────────────────


@router.post("/datasets/{dataset_id}/items", response_model=DatasetMeta)
async def add_items(dataset_id: str, body: AddItemsBody):
    items = [i.model_dump() for i in body.items]
    dataset = await add_items_to_dataset(dataset_id, items, allow_mismatch=body.allow_mismatch)
    return sanitize_dataset(dataset)


# ── Remove one item ────────────────────────────────────────────────────────────


@router.delete("/datasets/{dataset_id}/items/{item_id}", status_code=204)
async def remove_item(dataset_id: str, item_id: str):
    await remove_dataset_item(item_id)


# ── Delete dataset ─────────────────────────────────────────────────────────────


@router.delete("/datasets/{dataset_id}", status_code=204)
async def delete(dataset_id: str):
    await delete_dataset(dataset_id)


# ── Legacy mount shim (back-compat) ───────────────────────────────────────────


class MountBody(BaseModel):
    patient_data_path: str


@router.post("/sessions/{session_id}/datasets/mount", status_code=201, response_model=DatasetMeta)
async def mount(session_id: str, body: MountBody):
    return sanitize_dataset(await mount_dataset(session_id, body.patient_data_path))
