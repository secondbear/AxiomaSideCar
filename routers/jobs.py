from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from jobs.worker import enqueue_job, get_job, get_jobs_for_session
from schemas import JobStatus

router = APIRouter()


class CreateJobBody(BaseModel):
    type: str
    params: dict = {}


@router.get("/sessions/{session_id}/jobs", response_model=list[JobStatus])
async def list_jobs(session_id: str):
    return await get_jobs_for_session(session_id)


@router.post("/sessions/{session_id}/jobs", status_code=202, response_model=JobStatus)
async def create_job(session_id: str, body: CreateJobBody):
    return await enqueue_job(session_id, body.type, body.params)


@router.get("/jobs/{job_id}", response_model=JobStatus)
async def poll_job(job_id: str):
    job = await get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job
