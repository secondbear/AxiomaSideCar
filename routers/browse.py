"""Filesystem browse endpoint — lets the frontend navigate server-side directories."""

from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter()

# Restrict browsing to these roots for safety; empty list = unrestricted.
_ALLOWED_ROOTS: list[Path] = []


def _is_allowed(path: Path) -> bool:
    if not _ALLOWED_ROOTS:
        return True
    return any(path == root or root in path.parents for root in _ALLOWED_ROOTS)


class DirEntry(BaseModel):
    name: str
    path: str
    is_dir: bool
    size: int | None = None


class BrowseResult(BaseModel):
    path: str
    parent: str | None
    entries: list[DirEntry]


@router.get("/browse", response_model=BrowseResult)
async def browse(path: str = "/"):
    target = Path(path).resolve()

    if not target.exists():
        raise HTTPException(status_code=404, detail=f"Path not found: {path}")
    if not target.is_dir():
        raise HTTPException(status_code=422, detail=f"Not a directory: {path}")
    if not _is_allowed(target):
        raise HTTPException(status_code=403, detail="Path not in allowed roots")

    entries: list[DirEntry] = []
    try:
        for entry in sorted(target.iterdir(), key=lambda e: (not e.is_dir(), e.name.lower())):
            try:
                size = entry.stat().st_size if entry.is_file() else None
                entries.append(
                    DirEntry(
                        name=entry.name,
                        path=str(entry),
                        is_dir=entry.is_dir(),
                        size=size,
                    )
                )
            except PermissionError:
                continue
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc

    parent = str(target.parent) if target != target.parent else None
    return BrowseResult(path=str(target), parent=parent, entries=entries)
