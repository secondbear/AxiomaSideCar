# Copilot Chat Conversation Export: Architecture review and handoff summary

**User:** @secondbear
**Thread URL:** https://github.com/copilot/c/303a36ea-3deb-4ce2-85ca-41f0aa659d53

## @secondbear

You are an Expert Medical AI Architect and MLOps Engineer. Your task is to review and improve the architecture, repository structure, and dose calc backend and front end  pipeline for a multi machine deformable dose calculator.
the purpose is given in the repo docs but shortly is to calculate and simulate movement dose on CTs for radiotherapy.
review and list updates and archtechture improvements needed to move from half finished to research tool usable by a broad spectrum of researcher and several types of machines.
list here in the chat an extensive review and step by step handoff for implementing improvements to each of the repos. especielly the still not achiving goals on Truebeam VMAT and front end that is yet not operational.

## Copilot

Below is a full architecture review and a repo‑by‑repo, step‑by‑step handoff. It is based on `PLAN.md` / `AGENT_HANDOFF.md` / `docs/plan_truebeam_tb01.md` / `docs/lbte_implementation_plan.md` in GenDoseCalc, the `api/` + `gendosecalc/deform` tree in DeformCTMovement, and the `src/gateway` + README in AxiomaUX. (Code‑search results are capped at 10 hits, so the TrueBeam findings below may be incomplete — see [more results in code search](https://github.com/search?q=repo%3Asecondbear%2FGenDoseCalc+TrueBeam+VMAT&type=code).)

---

# 1. Executive assessment

| Area | State | Verdict |
|---|---|---|
| Radixact/Synchrony PB_v1 | Commissioned, γPTV 79–100% on sentinel plans, frozen | **Research‑usable** |
| PB_v2 | Implemented, K_e bug fixed, TERMA blocked on small‑field scatter | **Alpha** |
| TrueBeam / VMAT | Parser exists (`TrueBeamArchive`, `_parse_per_cp_segment_beam`), engine dispatch stub, commissioning YAML has `# MISSING DATA` | **Not achieving goals** |
| DeformCTMovement | deform package + FastAPI + Redis worker + Docker present | **Beta, but it vendors a copy of `gendosecalc/`** |
| AxiomaUX | 5 pages complete in mock mode only; `LocalDataGateway` has no server | **Not operational** |
| Cross‑repo | Three repos, two copies of `gendosecalc/`, no shared contract package, no published API schema | **Biggest structural risk** |

The single most damaging architectural fact: **`gendosecalc/` exists inside both GenDoseCalc and DeformCTMovement**, and AxiomaUX targets a FastAPI service that lives in DeformCTMovement (`api/main.py`, title `axioma-deform`) but is documented as spanning pycdms + GenDoseCalc + DeformCT. There is no single backend, no single package, and no versioned contract. Everything else downstream (broad researcher usability, multi‑machine support, working frontend) is blocked by that.

---

# 2. Target architecture

```
axiomics/                      (mono-repo or 4 pip-installable packages)
├── axioma-core/               shared contracts ONLY  (CT, Projection, DoseGrid,
│                              DeformationField, MachineSpec, JobSpec, JSON schemas)
├── gendosecalc/               dose engines (pb_v1 frozen, pb_v2, lbte) + io adapters
├── deformct/                  DVF generation/application  (imports axioma-core, NOT gendosecalc)
├── axioma-api/                ONE FastAPI service: sessions, datasets, jobs, slices,
│                              dose, dvh, gamma, commissioning, registration
└── AxiomaUX/                  frontend, consumes generated OpenAPI TS client
```

Rules to enforce:
1. `axioma-core` has zero heavy deps (numpy only). Everything else depends on it, never on each other's internals.
2. `deformct` must **not** contain a copy of `gendosecalc`. Delete it, add `gendosecalc>=x` as a dependency (or invert: deform becomes a subpackage of gendosecalc and DeformCTMovement holds only the API).
3. The API is generated‑contract‑first: FastAPI emits OpenAPI → `openapi-typescript` generates `src/gateway/generated/`. The frontend never hand‑writes DTOs.
4. Machine support is data, not code: `data/Commissioning/machines.yaml` + per‑machine YAML/CSV. Adding Elekta = one registry entry + commissioning folder + an archive adapter class.

---

# 3. GenDoseCalc — review and handoff

### 3.1 What's wrong architecturally
- **Repo bloat**: ~1 GB, 89% Jupyter Notebook by language share, `outputs/`, `runs/`, `example_data/` with full DICOM series in git. This makes the repo unusable for a "broad spectrum of researchers" — clone time and CI cost are prohibitive.
- **Documentation is a changelog, not a spec.** `AGENT_HANDOFF.md` is ~760 lines of dated run logs. New users cannot find "how do I run this on my machine's data".
- **Beam models scattered**: `example_data/radixact/beam_data/*.yaml` vs `data/Commissioning/RX01_*/beam_models/*.yaml`. Two roots for the same concept.
- **No results provenance schema**: `run_metadata.json` exists but is ad‑hoc; there is no versioned result manifest the UI can consume.
- **Magic constants still leak** (`_MU_TO_CGY_AT_DMAX`, `_RADIXACT_MU_PER_MINUTE = 850`, `_RTRECORD_TO_IEC_ANGLE_OFFSET_DEG`) — these are machine facts, not engine facts.

### 3.2 Step‑by‑step handoff — repo hygiene (do first, 1 week)
1. `git lfs` or an external data store for `example_data/`; keep only ≤50 MB of synthetic phantom + one 20‑slice anonymised CT in git. Add `outputs/`, `runs/` to `.gitignore` and purge from history with `git filter-repo`.
2. Split docs: `README.md` (10‑min quickstart), `docs/architecture.md`, `docs/machines/<id>.md`, `CHANGELOG.md` (move all dated `AGENT_HANDOFF` entries here). Keep `AGENT_HANDOFF.md` ≤100 lines: current state + next 5 actions only.
3. Move every beam model under one root: `data/commissioning/<machine_id>/{beam_models,beam_data}`. Delete the `example_data/radixact/beam_data` path and update `machine_version.py` fallbacks.
4. Convert notebooks to `scripts/` + `docs/notebooks/` with `nbstripout` in a pre‑commit hook. Notebooks must not be the primary interface.
5. Add `pip install gendosecalc[gpu]` smoke test to CI on a CPU runner (`use_gpu(False)`).

### 3.3 Step‑by‑step handoff — **TrueBeam / VMAT** (the stated gap)

The parser side is further along than the docs claim (`TrueBeamArchive.parse` is implemented, `_parse_per_cp_segment_beam` handles VMAT segment MU from `CumulativeMetersetWeight`). What is missing is (a) the engine path, (b) commissioning data, (c) VMAT‑specific geometry validation. Execute in this order:

**TB‑1 — Lock the regression gate (blocking).**
- Confirm `tests/regression/test_radixact_locked.py` exists and is bit‑equal on a 3‑projection fixture. If it doesn't, write it before touching `pencil_beam.py`.
- Assert `BeamModel.machine_type == "radixact"` dispatch exits before any TB code. CI must fail on any Radixact dose delta > 0.

**TB‑2 — Geometry & coordinate closeout for C‑arm.** This is where VMAT usually fails, not in the physics.
- TrueBeam is IEC 61217 native: gantry 0° = source **superior/anterior**, no 180° Accuray offset, no Y‑negation. Add explicit per‑machine `angle_offset_deg: 0.0`, `negate_iso_y: false` to `machines.yaml` and delete the module‑level constants from `raystation_rtplan.py`.
- Add collimator rotation. Radixact has none; TrueBeam VMAT plans routinely use 15–45° collimator. If `BeamLimitingDeviceAngle` is not applied to the MLC BEV grid, every VMAT plan will be wrong and gamma will look like a "physics" problem. **Check this first — it is the highest‑probability single cause of VMAT failure.**
- Add couch (`PatientSupportAngle`) handling, at minimum a hard error when ≠ 0.
- Write `tests/test_truebeam_geometry.py`: single 10×10 open field at gantry 0/90/180/270 with collimator 0/45 into a water cube; assert the field centroid and the MLC bank orientation land where analytics say.

**TB‑3 — MLC fluence model.** Millennium 120 is not a binary MLC.
- `gendosecalc/engine/mlc_fluence.py` must produce a continuous 2‑D BEV fluence from leaf positions (not open fractions): rounded leaf‑end transmission, DLG/leaf‑gap offset, tongue‑and‑groove between adjacent leaves, inter/intra‑leaf transmission, and the 5 mm/10 mm leaf‑width split of Millennium 120.
- Required commissioning inputs (put them in `data/commissioning/TB01/beam_data/`): `dlg.csv` (dosimetric leaf gap per energy), `mlc_transmission.csv`, `output_factors_sc_sp.csv`, `pdd_<energy>.csv`, `profiles_<fs>_<depth>.csv`, `wedge/`(if used).
- `scripts/tb01_inventory.py` should exit non‑zero on any `# MISSING DATA` field so the state is unambiguous.

**TB‑4 — VMAT arc discretisation.** Currently one Projection per CP pair with geometry from CP *i*.
- That biases every arc by half the gantry step (typically 2–4°). Use the **midpoint** gantry/MLC (average of CP *i* and *i+1*) for the segment, or add configurable sub‑sampling `--arc-subsample N`.
- Add a convergence test: dose vs `N ∈ {1,2,4,8}`; require < 0.5% change from N=4→8. Document the chosen default per machine.

**TB‑5 — Flat‑beam physics deltas vs FFF.**
- Off‑axis softening (`use_tb_off_axis_pdd_softening`) is mandatory for a flattened 6X beam — the horn/off‑axis spectrum differs qualitatively from Radixact 6FFF. Do not reuse the FFF `oar_2d` table.
- `use_tb_isf_1` (source‑to‑dmax scalar ISF) — pick one convention, document it, and add a unit test that reproduces a measured SSD‑90/100/110 output ratio.
- Extra‑focal/head‑scatter source with MLC occlusion (`use_tb_mlc_head_scatter_occlusion`) matters much more for IMRT/VMAT out‑of‑field than it does for a binary MLC.

**TB‑6 — Acceptance criteria (write them down and gate CI).**
| Test | Metric | Target |
|---|---|---|
| Water 10×10 PDD | vs measured, 20–300 mm | ±2% |
| Output factors 3×3 → 40×40 | vs measured | ±2% |
| Profiles, 5 depths × 3 field sizes | in‑field residual | ±2% / penumbra ±2 mm |
| Static IMRT phantom | γ 3%/3mm, 10% thresh | ≥95% |
| VMAT arc phantom | γ 3%/3mm | ≥95% |
| VMAT clinical (3 patients) | γ 3%/2mm in PTV | ≥90% |
| Radixact regression | bit‑equal | 100% |

**TB‑7 — Motion for TrueBeam.** Follow `docs/plan_motion_framework_2026-05-26.md`: timestamp projections from `CumulativeMetersetWeight` + dose rate, then `apply_motion()` from a generic CSV motion source. Do **not** build a Dynalog parser yet; a documented CSV schema (`t_ms, dx, dy, dz, rx, ry, rz`) makes the tool usable by researchers with KIM, Calypso, or optical data today.

### 3.4 Multi‑machine generalisation (what makes it a "research tool")
1. Formalise `MachineSpec` in `axioma-core`: `id, vendor, model, sad_mm, mlc {type, n_leaves, widths, dlg, transmission}, jaws, angle_conventions, dosimeter_unit, archive_adapter, beam_models[]`.
2. `gdc machine validate <id>` — checks the commissioning folder against the schema, prints a coverage table. This is the single command a new site runs on day one.
3. `gdc machine scaffold <vendor> <model>` — generates the YAML + empty CSVs + a `README` telling the physicist exactly which measurements to supply.
4. Add a third adapter (Elekta Agility RTPLAN) as a *proof of generality* — even read‑only geometry — before declaring the abstraction stable. Two implementations always look generic; three reveal the leaks.
5. Publish a **commissioning data contract** doc: one table per required measurement, units, geometry, tolerance.

---

# 4. DeformCTMovement — review and handoff

### 4.1 Findings
- Clean, well‑specified core (`models`, `sitk_bridge`, `dvf_io`, `dvf_generate`, `bone_mask`, `apply_deformation`) with synthetic‑only tests. Good.
- **It contains a `gendosecalc/` directory.** Duplicated engine = guaranteed drift. This is the top issue.
- The FastAPI service (`api/main.py`, title `axioma-deform`) plus `Dockerfile.worker`, `docker-compose.yml`, Redis job store, and routers `sessions|deform|stream` is in fact **the beginnings of the Axioma backend**, mis‑located inside a deformation library.
- `exampledata/` **and** `exemple_data` (typo) both present; `axioma_backend_spec.md:Zone.Identifier` (Windows ADS artefact) committed. `runs/` committed.
- Bone rigidity via HU threshold + Gaussian is fast but not invertible and not volume‑preserving; no Jacobian check.

### 4.2 Step‑by‑step handoff
1. **Extract the API.** Move `api/`, `Dockerfile*`, `docker-compose.yml`, `viewer/` into a new repo/package `axioma-api`. DeformCTMovement keeps only `gendosecalc/deform/` → rename to a standalone `deformct/` package with its own `pyproject.toml`.
2. **Delete the vendored `gendosecalc/`**; declare `gendosecalc = ">=X.Y"` in dependencies. Add a CI check that fails if `gendosecalc/` reappears.
3. Clean: remove `exemple_data`, `:Zone.Identifier`, `runs/`; unify to `example_data/`.
4. **DVF quality gates** (currently missing, and they matter clinically):
   - Compute and report Jacobian determinant; fail/flag if `min(J) ≤ 0` (folding).
   - Inverse‑consistency error after `InvertDisplacementField()` — assert < 0.5 mm on synthetic fields; expose as a metric in the API response.
   - Assert bone voxel HU histogram unchanged within tolerance after deformation.
   - Add a rigid‑body sanity test: known 5 mm translation must reproduce a 5 mm shift to sub‑voxel accuracy.
5. **Broaden beyond prostate/pelvis.** `bone_threshold_hu=300` + "bone rigid" is a pelvis assumption. Add a `RigidityModel` abstraction: `HUThresholdRigidity`, `MaskRigidity` (from RTSTRUCT ROI), `NoRigidity`. Thorax/liver users need ROI‑driven rigidity and lung‑density handling.
6. **Add real DIR, not just synthetic DVFs.** Wrap SimpleITK's demons/B‑spline (and optionally Elastix) behind `register(fixed_ct, moving_ct, config) -> DeformationField`. The AxiomaUX Adaptive page already expects a `register` job with RMS/Dice metrics; today there is nothing to serve it.
7. **Contour propagation + dose accumulation**: warp RTSTRUCT masks through the DVF, and accumulate dose by warping per‑fraction dose to the reference frame with energy/mass conservation (`dose × J` when mapping densities). Expose both as jobs.
8. DICOM export of the deformed CT series with new SeriesInstanceUID and correct `FrameOfReferenceUID` handling, plus a DICOM Registration (`1.2.840.10008.5.1.4.1.1.66.3`) / Deformable Registration object so other TPSs can read the result.

---

# 5. AxiomaUX — review and handoff (frontend not operational)

### 5.1 Root cause
The frontend is complete and correct in mock mode. It is not operational because **`LocalDataGateway` points at `localhost:8000`, and no service implements the full contract**. The nearest thing is `axioma-deform`, which serves only `sessions`, `deform`, `stream`. There is no `dose-calc`, no `dvh`, no `gamma`, no `commissioning`, no `register`. Additionally the contract is hand‑maintained in `src/gateway/DataGateway.ts` with a parallel mock — two sources of truth, guaranteed to diverge.

### 5.2 Step‑by‑step handoff (this is the critical path to a usable tool)

**FE‑0 — Freeze the contract.** Take `DataGateway.ts` + `backend_multi_axiomics.md` + `axioma_backend_spec.md` and produce **one** OpenAPI 3.1 document, versioned `/api/v1`. Everything below is generated from it.

**FE‑1 — Build `axioma-api` (FastAPI), promoted from `DeformCTMovement/api`.** Minimum endpoint set to make all five pages live:

| Group | Endpoints |
|---|---|
| Library | `GET /patients`, `GET /patients/{id}/sessions`, `POST /sessions`, `POST /sessions/{id}/datasets` (mount DICOM dir) |
| Volumes | `GET /datasets/{id}/meta`, `GET /datasets/{id}/slice?axis&index&window&level` (PNG/JPEG), `GET /datasets/{id}/volume` (chunked/zarr for VTK.js) |
| Structures | `GET /datasets/{id}/structures`, `GET /structures/{id}/contours?slice=` |
| Jobs | `POST /jobs/dose-calc`, `POST /jobs/register`, `POST /jobs/accumulate`, `POST /jobs/gamma`, `GET /jobs/{id}`, `GET /jobs/{id}/artifacts`, `DELETE /jobs/{id}` |
| Analysis | `GET /results/{id}/dvh`, `GET /results/{id}/gamma`, `GET /results/{id}/profile?axis&p0&p1` (Spyglass) |
| Commissioning | `GET /machines`, `GET /machines/{id}/beam-model`, `PUT /machines/{id}/beam-model`, `POST /jobs/water-phantom`, `GET /machines/{id}/golden-data` |
| Adaptive | `GET /sessions/{id}/fractions`, `GET /registrations/{id}` (RMS/Dice/Jacobian), `POST /registrations/{id}/contours/{roi}/accept` |

Keep the existing Redis + worker pattern; make the worker import `gendosecalc` and `deformct` directly. Long jobs must stream progress via SSE on `/jobs/{id}/events` — the frontend currently polls with TanStack Query, which is fine, but SSE removes the 3 s mock latency assumption.

**FE‑2 — Generate the client.** `openapi-typescript` → `src/gateway/generated/`. Rewrite `LocalDataGateway` as a thin adapter over the generated types. Rewrite `MockDataGateway` to satisfy the *same generated interface* so mock/real drift becomes a compile error.

**FE‑3 — Contract tests.** Run the FastAPI app in CI, hit every endpoint with a synthetic phantom session, and validate responses against the OpenAPI schema. Add a Playwright smoke test that boots API + UI and walks Library → Planning → run dose job → Analysis DVH.

**FE‑4 — Zero‑Payload done properly.** The stated principle is right but VTK.js volume rendering needs the voxels. Serve the volume as chunked, downsampled OME‑Zarr/NIfTI over HTTP range requests with a `?level=` pyramid, rather than either (a) full volume in JS memory or (b) per‑slice PNG for 3D. Define this explicitly in the spec.

**FE‑5 — Ship it.** Tauri sidecar packaging: bundle the FastAPI service as a PyInstaller binary, spawn from Rust, health‑check `/health`, show a real error state instead of silently falling back to mock. Add `VITE_USE_MOCK` to the UI as a visible badge — right now a researcher cannot tell whether the numbers are real.

**FE‑6 — Research‑grade essentials currently absent:** unit display and dose normalisation controls, per‑result provenance panel (engine version, beam model, git SHA, run metadata), CSV/JSON export of every chart, and a prominent "not for clinical use" banner in the viewport, not just the README.

---

# 6. Cross‑cutting: what turns this from half‑finished into a research tool

1. **One install path.** `pip install axiomics[all]` + `docker compose up` must give a working UI on synthetic data in under 10 minutes, with no clinical data and no GPU.
2. **A public, versioned example dataset** (synthetic phantom + one anonymised case) hosted outside git, fetched by `gdc fetch-example-data`. Today every tutorial depends on `J:\Synchrony`.
3. **Reproducibility manifest** written by every run: git SHA, engine version, beam‑model hash, CT UID, plan UID, all toggles, timing, hardware. The UI provenance panel and any publication depend on this.
4. **Versioning & freeze discipline.** PB_v1 frozen is good; formalise as `engine_version` in the result manifest and refuse to compare results across engine versions without an explicit flag.
5. **Testing pyramid.** Currently: many integration/regression runs, thin unit layer. Add per‑physics‑term unit tests (ISF, OAR, penumbra σ, T&G, ISL) so a regression tells you *which* term broke rather than "gamma dropped".
6. **Governance for a broad user base:** CONTRIBUTING, code of conduct, issue templates ("new machine request" template that asks for the commissioning data contract), semantic versioning, a citation file, and an explicit statement of what is validated vs experimental per machine.
7. **Privacy hardening**: the CI privacy scan is good; extend it to the API (reject serving PHI fields), and add a de‑identification job so researchers can onboard their own data safely.

---

# 7. Suggested sequencing

| Sprint | GenDoseCalc | DeformCT | AxiomaUX / API |
|---|---|---|---|
| 1 | Repo purge, docs split, one commissioning root | Delete vendored `gendosecalc`, extract `api/` | Freeze OpenAPI v1 |
| 2 | TB‑1 regression gate, TB‑2 collimator/geometry | `RigidityModel`, Jacobian gates | `axioma-api` skeleton: sessions, datasets, slices, jobs |
| 3 | TB‑3 MLC fluence + commissioning inventory | Real DIR (SimpleITK) + `register` job | Generated TS client, LocalDataGateway rewrite, Library+Planning live |
| 4 | TB‑4 arc subsampling, TB‑5 flat‑beam physics | Contour propagation, dose accumulation | Analysis + Commissioning pages live, SSE progress |
| 5 | TB‑6 acceptance suite green | DICOM registration export | Tauri sidecar packaging, Playwright E2E |
| 6 | `MachineSpec` + third vendor adapter | Adaptive metrics endpoint | Adaptive page live, provenance panel, export |

**If you only do three things:** (1) collimator‑rotation + midpoint‑arc geometry for VMAT — that is most likely why TrueBeam fails; (2) delete the duplicated `gendosecalc` and stand up one `axioma-api` with a generated OpenAPI client; (3) purge the repos of data/outputs so anyone outside your workstation can actually clone and run them.
