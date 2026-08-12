"""Dose service — wraps GenDoseCalc for clinical and commissioning calculations.

run_dose_calc   → gendosecalc.plan.clinical_run.ClinicalRunContext
    Computes planned static dose from an RTPLAN + CT.
    Called by the "dose-calc" job type.

run_phantom_calc → gendosecalc.plan.motion_dose.compute_motion_dose
    Computes motion-corrected dose using the logged actual isocenter per
    projection (delivered dose).
    Called by the "phantom-calc" job type and the commissioning endpoint.

Expected params keys
--------------------
run_dose_calc:
    rtplan_path       : str   – DICOM RTPLAN file
    ct_dir            : str   – DICOM CT folder
    target_spacing_mm : float (optional, default 2.5)

run_phantom_calc:
    rtplan_path       : str   – DICOM RTPLAN file
    ct_dir            : str   – DICOM CT folder
    motion_path       : str   – Synchrony MotionData.xml or CSV
    target_spacing_mm : float (optional, default 2.5)
    machineId         : str   (optional) – forwarded for logging only
"""

from pathlib import Path

from gendosecalc.motion import apply_motion, load_motion_source
from gendosecalc.plan.clinical_run import ClinicalRunContext

from services.provenance import normalize_provenance


def run_dose_calc(session_id: str, params: dict) -> dict:
    """Compute planned static dose.

    Builds a ClinicalRunContext (loads CT + RTPLAN, auto-selects beam model
    and IVDT) then runs compute_planned_static().

    Returns a summary dict with provenance and max/mean dose.
    """
    ctx = ClinicalRunContext.build(
        rtplan_path=Path(params["rtplan_path"]),
        ct_dir=Path(params["ct_dir"]),
        target_spacing_mm=float(params.get("target_spacing_mm", 2.5)),
    )
    dose_grid = ctx.compute_planned_static()

    return {
        "maxDoseGy": float(dose_grid.dose_gy.max()),
        "meanDoseGy": float(dose_grid.dose_gy.mean()),
        "provenance": normalize_provenance(session_id, ctx.provenance_dict()),
    }


def run_phantom_calc(params: dict) -> dict:
    """Compute motion-corrected (delivered) dose.

    Loads the motion source from motion_path, applies it to the projection
    list from the RTPLAN, then calls compute_motion_dose with actual
    isocenter positions per projection.

    Returns a summary dict with max/mean dose and the number of projections.
    """
    from gendosecalc.plan.motion_dose import compute_motion_dose

    ctx = ClinicalRunContext.build(
        rtplan_path=Path(params["rtplan_path"]),
        ct_dir=Path(params["ct_dir"]),
        target_spacing_mm=float(params.get("target_spacing_mm", 2.5)),
    )

    motion_source = load_motion_source(params["motion_path"])
    projections = apply_motion(ctx.projections, motion_source)

    dose_grid = compute_motion_dose(
        ct=ctx.ct,
        projections=projections,
        ivdt_name=ctx.ivdt_name,
        beam_model=ctx.beam_model,
    )

    return {
        "maxDoseGy": float(dose_grid.dose_gy.max()),
        "meanDoseGy": float(dose_grid.dose_gy.mean()),
        "nProjections": len(projections),
        "provenance": normalize_provenance(
            str(params.get("session_id", "")), ctx.provenance_dict()
        ),
    }
