# Engineering Specification: Axioma UI Studio (Frontend UX & Environment) v2.0
*Including Advanced Analytical Workflows & Synchronization*

## Project Overview
The `axioma-studio` repository houses the master controller and SOTA user interface for the GenDoseCalc/Axioma ecosystem. To handle massive data payloads (~10GB per session) efficiently, the interface adopts a 2026 design language emphasizing spatial minimalism, contextual controls, and hardware-accelerated WebGL graphics.

## Operating System & Build Environment Strategy
**Primary Recommendation: WSL2 / Native Linux**
Build and run the entire ecosystem (including the Tauri frontend) within WSL2 (Ubuntu 22.04+) or Native Linux. This ensures flawless `numpy.memmap` memory management, perfect file path consistency across the stack, and seamless interaction with the CUDA/CuPy backend workers. Tauri will pipe the native desktop window to Windows via WSLg.

---

## 1. The 2026 Design Language & UX Philosophy
* **Dark Mode by Default:** Reduces retinal fatigue and increases contrast for low-dose washes.
* **Spatial Minimalism:** Tools float as semi-transparent panels over the 3D viewport and dismiss themselves when not needed.
* **Typography:** Monospaced-hybrid fonts (e.g., Inter, JetBrains Mono) for data grids to ensure decimal alignment.
* **Non-Blocking Execution:** Background calculation jobs are represented by subtle pulsing indicators in the navigation bar.

---

## 2. Advanced Analytical Workflows & Synchronization
To elevate the system from a standard viewer to a SOTA research TPS, the UI implements deep, bi-directional state synchronization.

### A. The Linked Coordinate System (Dual Pointers)
When comparing two dose grids (e.g., Baseline vs. Fraction 1, or PBE vs. LBTE), the UI supports synchronized crosshairs.
* **How it works:** The React state (Zustand) holds a global `active_lps_coordinate: [x, y, z]`.
* **The UX:** When the user hovers the mouse over Viewport A (LBTE), VTK.js maps the screen pixel to a 3D physical coordinate. It updates the global state in milliseconds. Viewport B (PBE) listens to this state and renders a ghosted crosshair at the exact same anatomical point.
* **Camera Sync:** Panning, zooming, or scrolling slices in one viewport automatically replicates the camera transformation in the linked viewport.

### B. Bi-Directional UI Binding (Data <-> 3D)
Analytical tools are not static; they act as navigational controllers for the 3D space.
* **DVH to 3D Binding:** Hovering over a specific point on the DVH curve (e.g., "Rectum V60") highlights the corresponding voxels receiving that exact dose in the 3D VTK.js viewport.
* **Gamma to 3D Binding:** Clicking a specific row in the Gamma Analysis table (e.g., "PTV - 2%/2mm Failures") automatically calculates the center-of-mass of those failing voxels and snaps the Axial, Coronal, and Sagittal planes directly to that slice, rendering the failures as a bright red overlay.

### C. Longitudinal Cohort Viewer (Fraction Timeline)
To analyze intra-fraction motion across an entire treatment course (e.g., 7 fractions of the REMIND trial):
* **The Timeline Scrubber:** A sleek timeline sits at the bottom of the viewport matrix.
* **Small Multiples Grid:** The user can switch from a `2x2` MPR view to a `1x7` grid, displaying the same axial slice across all 7 fractions side-by-side.
* **Playback Mode:** The user can press "Play" on the timeline. The UI requests rapid, sequential memory-mapped slices from the FastAPI bridge, effectively animating the patient's anatomical changes and the resulting dose deformation over the 7-week treatment course.

### D. Engine-vs-Engine Differential Matrices
Comparing the fast Pencil Beam Engine (PBE) against the high-fidelity Linear Boltzmann Transport Equation (LBTE) engine is seamless.
* **Instant Subtraction:** With both calculations loaded in the session, pressing `D` (Difference) instantly computes $D_{LBTE} - D_{PBE}$ on the GPU and streams the difference array to the frontend.
* **Divergent Heatmaps:** The viewport transitions to a blue/white/red divergent color map (blue = PBE underdose, red = PBE overdose), allowing physicists to immediately spot where the fast engine breaks down near air cavities.

---

## 3. Global Workspace Layout
The screen real estate adapts to the active workflow:

1. **Left Drawer (The Library & Session Manager)**
   * Unified search for REMIND cohort patients.
   * Hierarchical list of active sessions, datasets, and background jobs.
2. **Center Stage (The Viewport Matrix)**
   * Powered by VTK.js. Switches between Single Volume, `2x2` MPR, Dual-Compare (Side-by-Side), or Longitudinal Grid (`1xN`).
3. **Right Panel (The Inspector)**
   * Contextual controls (DVH, Gamma, Window/Level, Material Overrides).
