from fastapi import APIRouter, HTTPException

from schemas import Patient
from services.privacy import sanitize_patient
from services.session_service import get_all_patients, get_patient_by_id

router = APIRouter()


@router.get("/patients", response_model=list[Patient])
async def list_patients():
    return [sanitize_patient(patient) for patient in await get_all_patients()]


@router.get("/patients/{patient_id}", response_model=Patient)
async def get_patient(patient_id: str):
    patient = await get_patient_by_id(patient_id)
    if patient is None:
        raise HTTPException(status_code=404, detail="Patient not found")
    return sanitize_patient(patient)
