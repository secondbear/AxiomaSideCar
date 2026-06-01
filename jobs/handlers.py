import asyncio

from services.dose_service import run_dose_calc, run_phantom_calc
from services.deform_service import run_registration, run_dose_accumulation


async def handle_dose_calc(session_id: str, params: dict, progress_cb) -> dict:
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, run_dose_calc, session_id, params)
    await progress_cb(1.0)
    return result


async def handle_register(session_id: str, params: dict, progress_cb) -> dict:
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, run_registration, session_id, params)
    await progress_cb(1.0)
    return result


async def handle_dose_accumulation(session_id: str, params: dict, progress_cb) -> dict:
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, run_dose_accumulation, session_id, params)
    await progress_cb(1.0)
    return result


async def handle_phantom_calc(session_id: str, params: dict, progress_cb) -> dict:
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, run_phantom_calc, params)
    await progress_cb(1.0)
    return result


HANDLERS: dict = {
    "dose-calc":         handle_dose_calc,
    "register":          handle_register,
    "dose-accumulation": handle_dose_accumulation,
    "phantom-calc":      handle_phantom_calc,
    # dvh and gamma are post-dose analytics — add when GenDoseCalc exposes them
}
