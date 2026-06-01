# Skill: Axioma Engine Integration

## When to use this skill
When writing or reviewing code that imports from DeformCTMovement, GenDoseCalc, or pycdms.
When adding new service functions in `services/`, new job handlers in `jobs/handlers.py`,
or new routes that trigger engine computation.

---

## Repo layout

```
~/repos/
├── AxiomaSideCar/          ← this repo (FastAPI orchestrator)
├── DeformCTMovement/       ← deformable CT generation
├── GenDoseCalc/
│   └── GenDoseCalc/        ← nested: the installable package is one level deeper
└── pycdms/                 ← CDMS file format parser only
```

All three are installed as editable packages into `.venv` via `requirements.txt`.
`DeformCTMovement` and `pycdms` install from the repo root; `GenDoseCalc` installs
from `../GenDoseCalc/GenDoseCalc` (nested layout).

---

## DeformCTMovement — import as `gendosecalc.deform`

Package name on disk: `gendosecalc.deform.*`
PyPI name: `gendosecalc-deform`

### Main entry point
```python
from gendosecalc.deform import generate_ensemble, DeformationConfig

manifest: dict = generate_ensemble(
    ct_dir=Path(params["ct_dir"]),         # reference DICOM CT folder
    motion_path=Path(params["motion_path"]),  # Synchrony XML or CSV
    out_dir=Path(params["out_dir"]),        # written: manifest.json + state_NNN_ct/
    config=DeformationConfig(),             # optional; n_states, bone_threshold_hu etc.
    rtstruct_path=params.get("rtstruct_path"),
)
```

Returns the manifest dict (also written to `out_dir/manifest.json`).

### Loading a pre-generated ensemble (for dose accumulation)
```python
from gendosecalc.io.deformed_ct_set import load_deformed_ct_set
deformed_set = load_deformed_ct_set(Path(params["deformed_dir"]))
# deformed_set.states  → list[DeformedCTState]
# deformed_set.config  → dict of generator parameters
```

### All public symbols from `gendosecalc.deform`
`DeformationConfig`, `DeformationField`, `MotionSamples`, `StateSelection`,
`EnsembleManifestEntry`, `load_dvf`, `save_dvf`, `ct_to_sitk`, `sitk_to_ct`,
`dvf_to_sitk`, `sitk_to_dvf`, `compute_bone_mask`, `compute_tissue_weight`,
`deform_ct`, `rigid_to_dvf`, `motion_log_entry_to_dvf`, `localized_rigid_to_dvf`,
`load_synchrony_xml`, `load_motion_csv`, `select_representative_states`,
`load_ctv_mask`, `deform_rtstruct`, `generate_ensemble`

---

## GenDoseCalc — import as `gendosecalc`

### Static (planned) dose
```python
from gendosecalc.plan.clinical_run import ClinicalRunContext

ctx = ClinicalRunContext.build(
    rtplan_path=Path(params["rtplan_path"]),
    ct_dir=Path(params["ct_dir"]),
    target_spacing_mm=float(params.get("target_spacing_mm", 2.5)),
    # beam_model_path=None  → auto-selected from machine_id in machines.yaml
    # ivdt_name="auto"      → auto-detected from CT
    # gpu_ids=None          → single default GPU or CPU
)
dose_grid = ctx.compute_planned_static()  # → DoseGrid
# dose_grid.dose_gy   → np.ndarray (nz, ny, nx) float32
# dose_grid.spacing_mm, dose_grid.origin_mm
```

### Motion-corrected (delivered) dose
```python
from gendosecalc.motion import load_motion_source, apply_motion
from gendosecalc.plan.motion_dose import compute_motion_dose

motion_source = load_motion_source(params["motion_path"])
projections = apply_motion(ctx.projections, motion_source)
dose_grid = compute_motion_dose(ct=ctx.ct, projections=projections,
                                ivdt_name=ctx.ivdt_name, beam_model=ctx.beam_model)
```

### Deformable dose accumulation
```python
from gendosecalc.plan.deformable_dose import compute_deformable_dose

dose_grid, report = compute_deformable_dose(
    planning_ct=ctx.ct,
    projections=ctx.projections,
    deformed_set=deformed_set,          # from load_deformed_ct_set()
    target_spacing_mm=2.5,
)
# report.as_dict()  → JSON-serialisable summary
```

### DVH and gamma analysis
```python
from gendosecalc.analysis.dvh import compute_dvh, load_structure_masks_from_rtstruct
from gendosecalc.analysis.gamma import compute_gamma

masks = load_structure_masks_from_rtstruct(rtstruct_path, dose_grid)
dvh_metrics = compute_dvh(dose_grid, masks)
# dvh_metrics["PTV"].d95_gy, .d50_gy, .d2_gy, .dmean_gy, .volume_cc

gamma_result = compute_gamma(reference_dose, evaluation_dose, dose_pct=2.0, dist_mm=1.0)
# gamma_result.pass_rate_pct, .gamma_map (np.ndarray), .max_gamma
```

### Machine registry (commissioning)
GenDoseCalc's `machines.yaml` is the ground truth for the dose engine.
The sidecar uses a **write-through pattern** — every SQLite machine write must also
call `_sync_to_gendosecalc()` in `routers/commissioning.py`.

```python
from gendosecalc.service.machines import list_machines, get_machine, add_machine, update_machine
from gendosecalc.service.beam_models import list_beam_model_versions, write_beam_model_version
```

---

## pycdms — file format parser only

pycdms does **not** manage patients or sessions. It parses CDMS archive files.
Patient/session identity lives in SQLite (`database.py`).

```python
from pycdms import scan_folder, group_by_fraction, detect_content, CdmsFile

files: list[CdmsFile] = scan_folder(Path(archive_dir))
# Each CdmsFile: .path, .content (ContentInfo with .kind), .meta (CdmsFileMeta)

by_fraction: dict[str, list[CdmsFile]] = group_by_fraction(files)

info = detect_content(Path(single_file))
# info.kind → e.g. "ct_series", "rtplan", "motion_xml", "raw_kv", "radiograph"
```

Used in `services/session_service.mount_dataset()` to classify an archive folder
before storing the path in the `datasets` table.

---

## The `run_in_executor` pattern

All engine calls are synchronous (CPU/GPU-bound). Always offload from async routes:

```python
import asyncio

async def my_route():
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, my_sync_engine_fn, arg1, arg2)
    return result
```

In job handlers (`jobs/handlers.py`) the pattern is identical — the handler itself
is `async` but calls the sync service function via `run_in_executor`.

---

## What NOT to do

- Do not call `gendosecalc.core` — there is no `.core` submodule.
- Do not call `deformct.core.DeformableRegistration` — that class does not exist.
- Do not call `pycdms.DataCatalogue` — that class does not exist.
- Do not construct `ClinicalRunContext` directly; always use `.build()`.
- Do not write directly to `machines.yaml`; always go through `gendosecalc.service.machines`.
