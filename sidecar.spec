# sidecar.spec — PyInstaller spec for the Axioma Sidecar binary
#
# Usage:
#   pip install pyinstaller
#   pyinstaller sidecar.spec --distpath axioma-sidecar/bin
#
# Output: axioma-sidecar/bin/axioma-sidecar  (single-file executable)
# Tauri references this path in src-tauri/tauri.conf.json as externalBin.
#
# One-file build is produced by passing a.binaries and a.datas directly into
# EXE (rather than into a COLLECT step).

from pathlib import Path
import sys

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

ROOT = Path(SPECPATH).resolve()
GEN_DOSE_ROOT = ROOT.parent / "GenDoseCalc" / "GenDoseCalc"
DEFORM_ROOT = ROOT.parent / "DeformCTMovement"

# ---------------------------------------------------------------------------
# Collect dynamic sub-modules that PyInstaller's static analysis misses
# ---------------------------------------------------------------------------
hiddenimports = [
    # uvicorn uses string-based dynamic imports for its protocol + loop backends
    "uvicorn.logging",
    "uvicorn.loops",
    "uvicorn.loops.auto",
    "uvicorn.loops.asyncio",
    "uvicorn.protocols",
    "uvicorn.protocols.http",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.http.httptools_impl",
    "uvicorn.protocols.websockets",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.protocols.websockets.wsproto_impl",
    "uvicorn.protocols.websockets.websockets_impl",
    "uvicorn.lifespan",
    "uvicorn.lifespan.off",
    "uvicorn.lifespan.on",
    # anyio back-ends (uvicorn/FastAPI concurrency)
    "anyio._backends._asyncio",
    "anyio._backends._trio",
    # pydantic v2 core
    "pydantic",
    "pydantic.v1",
    "pydantic_settings",
    # aiosqlite
    "aiosqlite",
    # python-multipart (FastAPI file uploads)
    "multipart",
    # email helpers used by some starlette internals
    "email.mime.text",
    "email.mime.multipart",
    "email.mime.base",
]

# Pull in every uvicorn sub-module to be safe
hiddenimports += collect_submodules("uvicorn")
sys.path.insert(0, str(DEFORM_ROOT))
hiddenimports += collect_submodules("gendosecalc.deform")
sys.path.pop(0)
sys.path.insert(0, str(GEN_DOSE_ROOT))
hiddenimports += collect_submodules("gendosecalc.analysis")
hiddenimports += collect_submodules("gendosecalc.plan")
hiddenimports += collect_submodules("gendosecalc")
sys.path.pop(0)
hiddenimports += collect_submodules("pycdms")
hiddenimports += collect_submodules("zarr")
hiddenimports += collect_submodules("skimage")

# ---------------------------------------------------------------------------
# Data files (none required at runtime — SQLite DB is created on first boot)
# ---------------------------------------------------------------------------
datas = []
datas += collect_data_files("uvicorn")  # uvicorn ships a few data files
datas.append((str(DEFORM_ROOT / "gendosecalc" / "deform"), "deform_package/gendosecalc/deform"))
COMMISSIONING_ROOT = GEN_DOSE_ROOT / "data" / "Commissioning"
_SAFE_MACHINE_SUBDIRS = (
    "beam_data",
    "beam_models",
    "geometry",
    "jaw",
    "mlc",
    "oar",
    "output_factors",
    "pdd",
    "scatter",
    "watertank",
)
_COMMISSIONING_ASSETS = [
    "machines.yaml",
    "jaw_calibration_RX01.yaml",
    "jaw_calibration_RX02.yaml",
] + [
    f"{machine}/{subdir}"
    for machine in ("RX01_newlinac", "RX01_oldlinac", "RX02")
    for subdir in _SAFE_MACHINE_SUBDIRS
]
for asset in _COMMISSIONING_ASSETS:
    source = COMMISSIONING_ROOT / asset
    if not source.exists():
        continue
    destination = Path("data/Commissioning") / asset
    datas.append((str(source), str(destination)))

# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------
a = Analysis(
    ["sidecar_entry.py"],
    pathex=[
        str(ROOT),
        str(ROOT.parent / "GenDoseCalc" / "GenDoseCalc"),
        str(ROOT.parent / "DeformCTMovement"),
    ],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # These are never needed at runtime — save ~40 MB
        "tkinter",
        "matplotlib",
        "IPython",
        "jupyter",
        "notebook",
        "pytest",
        "ruff",
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="axioma-sidecar",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    # Passing a.binaries + a.datas into EXE (not COLLECT) produces a one-file binary
)
