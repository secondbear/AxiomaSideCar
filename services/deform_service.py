"""Deform service — wraps DeformCTMovement and the deformable dose pipeline.

run_registration  → gendosecalc.deform.generate_ensemble
    Generates an ensemble of deformed CTs from a reference CT + motion log.
    Called by the "register" job type and the /adaptive registrations endpoint.

run_dose_accumulation  → gendosecalc.plan.deformable_dose.compute_deformable_dose
    Loads the pre-generated deformed-CT set produced by run_registration and
    accumulates dose across all motion states.
    Called by the "dose-accumulation" job type.

Expected params keys
--------------------
run_registration:
    ct_dir       : str  – path to reference DICOM CT folder
    motion_path  : str  – path to Synchrony MotionData.xml or CSV
    out_dir      : str  – output directory (manifest + deformed CTs written here)
    n_states     : int  (optional) – override number of representative states
    rtstruct_path: str  (optional) – RTSTRUCT for CTV localisation

run_dose_accumulation:
    rtplan_path  : str  – DICOM RTPLAN file
    ct_dir       : str  – reference CT folder (same as used for registration)
    deformed_dir : str  – out_dir from a previous run_registration run
    target_spacing_mm : float (optional, default 2.5)
"""
from pathlib import Path

from gendosecalc.deform import generate_ensemble, DeformationConfig
from gendosecalc.io.deformed_ct_set import load_deformed_ct_set
from gendosecalc.plan.clinical_run import ClinicalRunContext
from gendosecalc.plan.deformable_dose import compute_deformable_dose


def run_registration(session_id: str, params: dict) -> dict:
    """Generate a deformed-CT ensemble from a reference CT and motion log.

    Returns the manifest dict (also written to out_dir/manifest.json).
    """
    config = DeformationConfig()
    if "n_states" in params:
        config.n_states = int(params["n_states"])

    manifest = generate_ensemble(
        ct_dir=Path(params["ct_dir"]),
        motion_path=Path(params["motion_path"]),
        out_dir=Path(params["out_dir"]),
        config=config,
        rtstruct_path=params.get("rtstruct_path"),
        deform_rtstruct_path=params.get("rtstruct_path"),  # deform the same RS
    )
    return {"manifest": manifest, "out_dir": params["out_dir"]}


def run_dose_accumulation(session_id: str, params: dict) -> dict:
    """Accumulate deformable dose using a pre-generated ensemble.

    Loads the deformed-CT set from deformed_dir, builds a ClinicalRunContext
    from the RTPLAN + CT, then runs compute_deformable_dose.

    Returns a serialisable summary dict from DeformableDoseReport.
    """
    target_spacing = float(params.get("target_spacing_mm", 2.5))

    ctx = ClinicalRunContext.build(
        rtplan_path=Path(params["rtplan_path"]),
        ct_dir=Path(params["ct_dir"]),
        target_spacing_mm=target_spacing,
    )

    deformed_set = load_deformed_ct_set(Path(params["deformed_dir"]))

    _dose_grid, report = compute_deformable_dose(
        planning_ct=ctx.ct,
        projections=ctx.projections,
        deformed_set=deformed_set,
        target_spacing_mm=target_spacing,
    )

    return report.as_dict()

