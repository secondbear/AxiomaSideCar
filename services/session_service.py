import asyncio
from pycdms import DataCatalogue  # adjust to actual pycdms public API

_catalogue = DataCatalogue()


# ── Patients ──────────────────────────────────────────────────────────────────

async def get_all_patients() -> list:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _catalogue.list_patients)


async def get_patient_by_id(patient_id: str) -> dict | None:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _catalogue.get_patient, patient_id)


# ── Sessions ──────────────────────────────────────────────────────────────────

async def get_sessions_for_patient(patient_id: str) -> list:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _catalogue.list_sessions, patient_id)


async def create_session(patient_id: str, label: str) -> dict:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _catalogue.create_session, patient_id, label)


async def get_session_by_id(session_id: str) -> dict | None:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _catalogue.get_session, session_id)


# ── Datasets ──────────────────────────────────────────────────────────────────

async def get_datasets_for_session(session_id: str) -> list:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _catalogue.list_datasets, session_id)


async def mount_dataset(session_id: str, patient_data_path: str) -> dict:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None, _catalogue.mount_dataset, session_id, patient_data_path
    )
