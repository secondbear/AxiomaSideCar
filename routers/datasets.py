from fastapi import APIRouter
from pydantic import BaseModel
from services.session_service import get_datasets_for_session, mount_dataset

router = APIRouter()


class MountBody(BaseModel):
    patient_data_path: str


@router.get("/sessions/{session_id}/datasets")
async def list_datasets(session_id: str):
    return await get_datasets_for_session(session_id)


@router.post("/sessions/{session_id}/datasets/mount", status_code=201)
async def mount(session_id: str, body: MountBody):
    return await mount_dataset(session_id, body.patient_data_path)
