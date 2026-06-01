from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from config import settings
from database import init_db
from routers import adaptive, commissioning, datasets, jobs, patients, sessions, slices


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield


app = FastAPI(title="Axioma Sidecar", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.cors_origin],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(patients.router, prefix="/api/v1")
app.include_router(sessions.router, prefix="/api/v1")
app.include_router(datasets.router, prefix="/api/v1")
app.include_router(slices.router, prefix="/api/v1")
app.include_router(jobs.router, prefix="/api/v1")
app.include_router(commissioning.router, prefix="/api/v1")
app.include_router(adaptive.router, prefix="/api/v1")
