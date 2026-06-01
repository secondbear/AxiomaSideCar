from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from schemas import Session
from services.session_service import (
    create_session,
    get_session_by_id,
    get_sessions_for_patient,
)

router = APIRouter()


class CreateSessionBody(BaseModel):
    patient_id: str
    label: str


@router.get("/sessions", response_model=list[Session])
async def list_sessions(patient_id: str):
    return await get_sessions_for_patient(patient_id)


@router.post("/sessions", status_code=201, response_model=Session)
async def create_new_session(body: CreateSessionBody):
    return await create_session(body.patient_id, body.label)


@router.get("/sessions/{session_id}", response_model=Session)
async def get_session(session_id: str):
    session = await get_session_by_id(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return session
