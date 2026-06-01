# Adjust import path to match GenDoseCalc's actual package name
from gendosecalc.core import DoseEngine, WaterPhantomMode


def run_dose_calc(session_id: str, params: dict) -> dict:
    """Synchronous — called via run_in_executor from jobs/handlers.py."""
    engine = DoseEngine(session_id=session_id)
    result = engine.calculate(**params)
    return {"dose_dataset_id": result.output_path}


def run_phantom_calc(params: dict) -> dict:
    """Water-phantom PDD/profile calculation for commissioning."""
    engine = WaterPhantomMode(
        machine_id=params["machineId"],
        engine=params["engine"],
        parameters=params["parameters"],
    )
    result = engine.run()
    return {
        "pdd":           result.pdd,
        "profileDmax":   result.profile_dmax,
        "profile10cm":   result.profile_10cm,
        "outputFactors": result.output_factors,
    }
