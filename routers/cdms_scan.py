"""CDMS folder scanner — inventories .cdms files and groups them by fraction/type.

GET /api/v1/cdms/scan?path=<absolute_dir>

Uses pycdms.scan_folder + group_by_fraction + parser.detect_content to return
a structured tree: archive → fractions → type-groups, each selectable as a
dataset item (kind='cdms_group').

Consideration #3: CDMS files containing embedded DICOM (type_code 99B0, format=dicom)
are treated as CDMS items only — the embedded DICOM is NOT unwrapped into the DICOM tree.
"""

import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pycdms.discovery import group_by_fraction, scan_folder
from pycdms.models import TYPE_DESCRIPTIONS
from pycdms.parser import detect_content
from pydantic import BaseModel

router = APIRouter(prefix="/cdms")

# Use a small thread pool for parallel content detection
_EXECUTOR = ThreadPoolExecutor(max_workers=4)


class CdmsFileInfo(BaseModel):
    path: str
    seq: int
    type_code: str
    type_name: str
    format: str  # 'dicom' | 'xml' | 'raw_kv' | 'text' | 'binary' | 'empty'
    format_detail: str  # SOP UID for DICOM, XML root tag, etc.
    size_bytes: int | None = None


class CdmsTypeGroup(BaseModel):
    """All files of the same type_code within one fraction."""

    type_code: str
    type_name: str
    file_count: int
    # format summary (e.g. what fraction are DICOM vs XML)
    dominant_format: str
    machine: str | None = None
    files: list[CdmsFileInfo]


class CdmsFraction(BaseModel):
    """One fraction/session timestamp (key from group_by_fraction)."""

    fraction_key: str  # e.g. '260101120000' (timestamp string from filename)
    total_files: int
    type_groups: list[CdmsTypeGroup]


class CdmsScanResult(BaseModel):
    root_path: str
    total_cdms_files: int
    fractions: list[CdmsFraction]
    scan_time_ms: float
    truncated: bool


@router.get("/scan", response_model=CdmsScanResult)
async def scan_cdms(path: str, max_files: int = 5000):
    root = Path(path).resolve()
    if not root.exists():
        raise HTTPException(status_code=404, detail=f"Path not found: {path}")
    if not root.is_dir():
        raise HTTPException(status_code=422, detail=f"Not a directory: {path}")

    t0 = time.perf_counter()

    # scan_folder is synchronous and may be slow on large archives
    import asyncio

    loop = asyncio.get_event_loop()
    all_files = await loop.run_in_executor(None, scan_folder, root)

    truncated = len(all_files) > max_files
    if truncated:
        all_files = all_files[:max_files]

    # Group by fraction
    by_fraction = group_by_fraction(all_files)

    fractions: list[CdmsFraction] = []
    for frac_key, frac_files in by_fraction.items():
        # Sub-group by type_code within this fraction
        by_type: dict[str, list] = {}
        for cf in frac_files:
            tc = cf.meta.type_code if cf.meta else "UNKN"
            by_type.setdefault(tc, []).append(cf)

        type_groups: list[CdmsTypeGroup] = []
        for tc, tc_files in sorted(by_type.items()):
            file_infos: list[CdmsFileInfo] = []
            formats: list[str] = []

            for cf in tc_files:
                try:
                    ci = detect_content(cf.path)
                    fmt = ci.format
                    fmt_detail = ci.detail
                except Exception:
                    fmt, fmt_detail = "binary", ""
                formats.append(fmt)
                try:
                    sz = cf.path.stat().st_size
                except Exception:
                    sz = None
                file_infos.append(
                    CdmsFileInfo(
                        path=str(cf.path),
                        seq=cf.seq,
                        type_code=tc,
                        type_name=TYPE_DESCRIPTIONS.get(tc, f"unknown_{tc}"),
                        format=fmt,
                        format_detail=fmt_detail,
                        size_bytes=sz,
                    )
                )

            dominant_format = max(set(formats), key=formats.count) if formats else "binary"
            machine = tc_files[0].meta.machine if tc_files[0].meta else None

            type_groups.append(
                CdmsTypeGroup(
                    type_code=tc,
                    type_name=TYPE_DESCRIPTIONS.get(tc, f"unknown_{tc}"),
                    file_count=len(tc_files),
                    dominant_format=dominant_format,
                    machine=machine,
                    files=file_infos,
                )
            )

        fractions.append(
            CdmsFraction(
                fraction_key=frac_key,
                total_files=len(frac_files),
                type_groups=type_groups,
            )
        )

    elapsed_ms = (time.perf_counter() - t0) * 1000
    return CdmsScanResult(
        root_path=str(root),
        total_cdms_files=len(all_files),
        fractions=fractions,
        scan_time_ms=round(elapsed_ms, 1),
        truncated=truncated,
    )
