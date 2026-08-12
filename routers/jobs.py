import asyncio
import json

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from jobs.worker import cancel_job, enqueue_job, get_job, get_job_artifacts, get_jobs_for_session
from schemas import JobArtifact, JobStatus

router = APIRouter()


class CreateJobBody(BaseModel):
    type: str
    params: dict = Field(default_factory=dict)


REQUIRED_PARAMS = {
    "dose-calc": {"rtplan_path", "ct_dir"},
    "dvh-calc": {"rtplan_path", "ct_dir", "rtstruct_path"},
    "dvh": {"rtplan_path", "ct_dir", "rtstruct_path"},
    "gamma-calc": {
        "reference_rtplan_path",
        "reference_ct_dir",
        "evaluation_rtplan_path",
        "evaluation_ct_dir",
        "motion_path",
    },
    "gamma": {
        "reference_rtplan_path",
        "reference_ct_dir",
        "evaluation_rtplan_path",
        "evaluation_ct_dir",
        "motion_path",
    },
    "register": {"ct_dir", "motion_path", "out_dir"},
    "dose-accumulation": {"rtplan_path", "ct_dir", "deformed_dir"},
    "phantom-calc": {"rtplan_path", "ct_dir", "motion_path"},
    "deidentify": {"source_dir", "output_dir"},
}


@router.get("/sessions/{session_id}/jobs", response_model=list[JobStatus])
async def list_jobs(session_id: str):
    return await get_jobs_for_session(session_id)


@router.post("/sessions/{session_id}/jobs", status_code=202, response_model=JobStatus)
async def create_job(session_id: str, body: CreateJobBody):
    required = REQUIRED_PARAMS.get(body.type)
    if required is None:
        raise HTTPException(status_code=422, detail=f"Unsupported job type: {body.type!r}")
    missing = sorted(required - body.params.keys())
    if missing:
        raise HTTPException(
            status_code=422,
            detail={"message": "Missing required job parameters", "missing": missing},
        )
    return await enqueue_job(session_id, body.type, body.params)


@router.get("/jobs/{job_id}", response_model=JobStatus)
async def poll_job(job_id: str):
    job = await get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@router.delete("/jobs/{job_id}", response_model=JobStatus)
async def delete_job(job_id: str):
    job = await get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if job["status"] in {"completed", "failed", "cancelled"}:
        return job
    return await cancel_job(job_id)


@router.get("/jobs/{job_id}/artifacts", response_model=list[JobArtifact])
async def list_job_artifacts(job_id: str):
    if await get_job(job_id) is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return await get_job_artifacts(job_id)


@router.get("/jobs/{job_id}/events")
async def job_events(job_id: str, interval_ms: int = 100):
    if await get_job(job_id) is None:
        raise HTTPException(status_code=404, detail="Job not found")

    interval_s = min(max(interval_ms / 1000, 0.05), 5.0)

    async def stream():
        previous = None
        while True:
            job = await get_job(job_id)
            if job is None:
                return
            snapshot = {
                "id": job["id"],
                "status": job["status"],
                "progress": job["progress"],
                "message": job["message"],
                "updated_at": job["updated_at"],
            }
            marker = tuple(snapshot.values())
            if marker != previous:
                yield f"event: job\ndata: {json.dumps(snapshot)}\n\n"
                previous = marker
            if job["status"] in {"completed", "failed", "cancelled"}:
                return
            await asyncio.sleep(interval_s)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
