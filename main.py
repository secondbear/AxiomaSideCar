from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from database import init_db
from routers import patients, sessions, datasets, slices, jobs, commissioning, adaptive


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield


app = FastAPI(title="Axioma Sidecar", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:1420"],  # AxiomaUX Tauri dev server
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(patients.router,       prefix="/api/v1")
app.include_router(sessions.router,       prefix="/api/v1")
app.include_router(datasets.router,       prefix="/api/v1")
app.include_router(slices.router,         prefix="/api/v1")
app.include_router(jobs.router,           prefix="/api/v1")
app.include_router(commissioning.router,  prefix="/api/v1")
app.include_router(adaptive.router,       prefix="/api/v1")
