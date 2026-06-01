from deformct.core import DeformableRegistration


def run_registration(session_id: str, params: dict) -> dict:
    """Synchronous — called via run_in_executor from jobs/handlers.py."""
    reg = DeformableRegistration(session_id=session_id)
    result = reg.register_all_fractions(**params)
    # result.registrations: list of {fractionIndex, rmsSurfaceDistanceMm, meanDice}
    return {"registrations": result.registrations}


def run_dose_accumulation(session_id: str, params: dict) -> dict:
    """Runs DeformCT DVF warp + GenDoseCalc DVH rollup."""
    reg = DeformableRegistration(session_id=session_id)
    result = reg.accumulate_dose(**params)
    return {
        "patientId":               result.patient_id,
        "includedFractionIndices": result.included_fractions,
        "totalPrescriptionGy":     result.prescription_gy,
        "structures":              result.structures,
    }
