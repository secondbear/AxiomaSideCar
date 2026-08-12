import asyncio
from pathlib import Path

from services import deform_service, dose_service
from services.deidentify_service import deidentify_dicom_tree


async def handle_dose_calc(session_id: str, params: dict, progress_cb) -> dict:
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, dose_service.run_dose_calc, session_id, params)
    await progress_cb(1.0)
    return result


async def handle_register(session_id: str, params: dict, progress_cb) -> dict:
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, deform_service.run_registration, session_id, params)
    await progress_cb(1.0)
    return result


async def handle_dose_accumulation(session_id: str, params: dict, progress_cb) -> dict:
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(
        None, deform_service.run_dose_accumulation, session_id, params
    )
    await progress_cb(1.0)
    return result


async def handle_phantom_calc(session_id: str, params: dict, progress_cb) -> dict:
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(
        None, dose_service.run_phantom_calc, {**params, "session_id": session_id}
    )
    await progress_cb(1.0)
    return result


def _run_dvh_calc(params: dict) -> dict:
    """Compute DVH metrics for all structures in the given RTSTRUCT.

    Re-uses the dose computation so no intermediate file is needed.

    Required params:
        rtplan_path   : str – DICOM RTPLAN file
        ct_dir        : str – DICOM CT folder
        rtstruct_path : str – DICOM RTSTRUCT file
        target_spacing_mm : float (optional, default 2.5)
    """
    from gendosecalc.analysis.dvh import compute_dvh, load_structure_masks_from_rtstruct
    from gendosecalc.plan.clinical_run import ClinicalRunContext

    ctx = ClinicalRunContext.build(
        rtplan_path=Path(params["rtplan_path"]),
        ct_dir=Path(params["ct_dir"]),
        target_spacing_mm=float(params.get("target_spacing_mm", 2.5)),
    )
    dose_grid = ctx.compute_planned_static()
    masks = load_structure_masks_from_rtstruct(params["rtstruct_path"], dose_grid)
    dvh = compute_dvh(dose_grid, masks)
    return {
        name: {
            "structureName": m.structure_name,
            "d95Gy": m.d95_gy,
            "d50Gy": m.d50_gy,
            "d2Gy": m.d2_gy,
            "dmeanGy": m.dmean_gy,
            "volumeCc": m.volume_cc,
        }
        for name, m in dvh.items()
    }


def _run_gamma_calc(params: dict) -> dict:
    """Compute gamma pass rate between planned and motion-corrected dose.

    Required params:
        reference_rtplan_path : str
        reference_ct_dir      : str
        evaluation_rtplan_path: str
        evaluation_ct_dir     : str
        motion_path           : str – for the evaluation (delivered) dose
        dose_pct              : float (optional, default 2.0)
        dist_mm               : float (optional, default 1.0)
        target_spacing_mm     : float (optional, default 2.5)
    """
    from gendosecalc.analysis.gamma import compute_gamma
    from gendosecalc.motion import apply_motion, load_motion_source
    from gendosecalc.plan.clinical_run import ClinicalRunContext
    from gendosecalc.plan.motion_dose import compute_motion_dose

    spacing = float(params.get("target_spacing_mm", 2.5))

    ref_ctx = ClinicalRunContext.build(
        rtplan_path=Path(params["reference_rtplan_path"]),
        ct_dir=Path(params["reference_ct_dir"]),
        target_spacing_mm=spacing,
    )
    reference = ref_ctx.compute_planned_static()

    eval_ctx = ClinicalRunContext.build(
        rtplan_path=Path(params["evaluation_rtplan_path"]),
        ct_dir=Path(params["evaluation_ct_dir"]),
        target_spacing_mm=spacing,
    )
    motion_source = load_motion_source(params["motion_path"])
    projections = apply_motion(eval_ctx.projections, motion_source)
    evaluation = compute_motion_dose(
        ct=eval_ctx.ct,
        projections=projections,
        ivdt_name=eval_ctx.ivdt_name,
        beam_model=eval_ctx.beam_model,
    )

    result = compute_gamma(
        reference,
        evaluation,
        dose_pct=float(params.get("dose_pct", 2.0)),
        dist_mm=float(params.get("dist_mm", 1.0)),
    )
    return {
        "passRatePct": result.pass_rate_pct,
        "maxGamma": result.max_gamma,
        "doseThresholdPct": result.dose_threshold_pct,
        "distanceThresholdMm": result.distance_threshold_mm,
        "localGamma": result.local_gamma,
    }


async def handle_dvh_calc(session_id: str, params: dict, progress_cb) -> dict:
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, _run_dvh_calc, params)
    await progress_cb(1.0)
    return result


async def handle_gamma_calc(session_id: str, params: dict, progress_cb) -> dict:
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, _run_gamma_calc, params)
    await progress_cb(1.0)
    return result


async def handle_deidentify(session_id: str, params: dict, progress_cb) -> dict:
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(
        None, deidentify_dicom_tree, params["source_dir"], params["output_dir"]
    )
    await progress_cb(1.0)
    return result


HANDLERS: dict = {
    "dose-calc": handle_dose_calc,
    "register": handle_register,
    "dose-accumulation": handle_dose_accumulation,
    "phantom-calc": handle_phantom_calc,
    "dvh-calc": handle_dvh_calc,
    "gamma-calc": handle_gamma_calc,
    "dvh": handle_dvh_calc,
    "gamma": handle_gamma_calc,
    "deidentify": handle_deidentify,
}
