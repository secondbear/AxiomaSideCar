from contextlib import asynccontextmanager

import aiosqlite
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

import database
from config import settings
from database import init_db
from jobs.worker import start_worker, stop_worker
from routers import (
    adaptive,
    browse,
    cdms_scan,
    commissioning,
    datasets,
    dicom_scan,
    jobs,
    patients,
    sessions,
    slices,
    structures,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    await start_worker()
    try:
        yield
    finally:
        await stop_worker()


app = FastAPI(title="Axioma Sidecar", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.cors_origin],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health", tags=["system"])
async def health() -> dict[str, str]:
    """Report whether the API can reach its local persistence store."""
    try:
        async with aiosqlite.connect(database.DB_PATH) as db:
            await db.execute("SELECT 1")
    except Exception:
        return {"status": "degraded", "database": "unavailable"}
    return {"status": "ok", "database": "ok"}


app.include_router(patients.router, prefix="/api/v1")
app.include_router(sessions.router, prefix="/api/v1")
app.include_router(datasets.router, prefix="/api/v1")
app.include_router(slices.router, prefix="/api/v1")
app.include_router(structures.router, prefix="/api/v1")
app.include_router(jobs.router, prefix="/api/v1")
app.include_router(commissioning.router, prefix="/api/v1")
app.include_router(adaptive.router, prefix="/api/v1")
app.include_router(browse.router, prefix="/api/v1")
app.include_router(dicom_scan.router, prefix="/api/v1")
app.include_router(cdms_scan.router, prefix="/api/v1")
