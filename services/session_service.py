"""Session service — patient/session/dataset CRUD backed by SQLite.

Dataset model (post-redesign):
  - datasets table: study-level container (label, kind, patient identity, study UID).
  - dataset_items table: one row per DICOM series or CDMS fraction/type-group.
  - Items from multiple source paths can be added to the same dataset (incremental).
  - DICOM deduplication: same SeriesInstanceUID → merge file lists instead of duplicating.
  - Study binding: dataset's study_instance_uid is locked to first DICOM item; later
    adds with a different UID raise 409 unless allow_mismatch=True.
"""

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path

import aiosqlite
from fastapi import HTTPException

from database import DB_PATH

try:
    from pycdms import scan_folder
except ImportError:
    scan_folder = None

# ── Patients ──────────────────────────────────────────────────────────────────


async def get_all_patients() -> list:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM patients ORDER BY created_at") as cur:
            rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def get_patient_by_id(patient_id: str) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM patients WHERE id=?", (patient_id,)) as cur:
            row = await cur.fetchone()
    return dict(row) if row else None


# ── Sessions ──────────────────────────────────────────────────────────────────


async def get_sessions_for_patient(patient_id: str) -> list:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM sessions WHERE patient_id=? ORDER BY created_at",
            (patient_id,),
        ) as cur:
            rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def create_session(patient_id: str, label: str) -> dict:
    session_id = str(uuid.uuid4())
    now = datetime.now(UTC).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO sessions (id, patient_id, label, created_at, updated_at) "
            "VALUES (?,?,?,?,?)",
            (session_id, patient_id, label, now, now),
        )
        await db.commit()
    return {
        "id": session_id,
        "patient_id": patient_id,
        "label": label,
        "created_at": now,
        "updated_at": now,
    }


async def get_session_by_id(session_id: str) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM sessions WHERE id=?", (session_id,)) as cur:
            row = await cur.fetchone()
    return dict(row) if row else None


# ── Datasets ──────────────────────────────────────────────────────────────────


def _infer_kind(items: list[dict]) -> str:
    kinds = {i.get("kind", "") for i in items}
    has_dicom = "dicom_series" in kinds
    has_cdms = "cdms_group" in kinds
    if has_dicom and has_cdms:
        return "mixed"
    if has_cdms:
        return "cdms"
    return "dicom"


async def _build_dataset_response(db: aiosqlite.Connection, dataset_id: str) -> dict:
    db.row_factory = aiosqlite.Row
    async with db.execute("SELECT * FROM datasets WHERE id=?", (dataset_id,)) as cur:
        row = await cur.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Dataset not found")
    ds = dict(row)
    async with db.execute(
        "SELECT * FROM dataset_items WHERE dataset_id=? ORDER BY added_at", (dataset_id,)
    ) as cur:
        items = [dict(r) for r in await cur.fetchall()]
    ds["items"] = items
    return ds


async def get_datasets_for_session(session_id: str) -> list:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM datasets WHERE session_id=? ORDER BY created_at",
            (session_id,),
        ) as cur:
            datasets = [dict(r) for r in await cur.fetchall()]
        for ds in datasets:
            async with db.execute(
                "SELECT * FROM dataset_items WHERE dataset_id=? ORDER BY added_at",
                (ds["id"],),
            ) as cur:
                ds["items"] = [dict(r) for r in await cur.fetchall()]
    return datasets


async def create_dataset(
    session_id: str,
    label: str,
    items: list[dict],
    patient_name: str | None = None,
    patient_id: str | None = None,
    study_instance_uid: str | None = None,
    frame_of_reference_uid: str | None = None,
    content_type: str = "",
) -> dict:
    now = datetime.now(UTC).isoformat()
    dataset_id = str(uuid.uuid4())
    kind = _infer_kind(items)

    # Auto-infer study identity from first DICOM item if not provided
    for item in items:
        if item.get("kind") == "dicom_series":
            if not patient_name:
                patient_name = item.get("patient_name")
            if not patient_id:
                patient_id = item.get("patient_id")
            if not study_instance_uid:
                study_instance_uid = item.get("study_instance_uid")
            if not frame_of_reference_uid:
                frame_of_reference_uid = item.get("frame_of_reference_uid")
            break

    # Pick first source path as legacy compat path
    first_path = items[0]["source_path"] if items else ""

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO datasets
               (id, session_id, label, kind, patient_name, patient_id,
                study_instance_uid, frame_of_reference_uid, path, content_type,
                created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                dataset_id,
                session_id,
                label,
                kind,
                patient_name,
                patient_id,
                study_instance_uid,
                frame_of_reference_uid,
                first_path,
                content_type or (items[0].get("content_type", "") if items else ""),
                now,
                now,
            ),
        )
        await db.commit()
        await _insert_items(db, dataset_id, items, now)
        result = await _build_dataset_response(db, dataset_id)
    return result


async def add_items_to_dataset(
    dataset_id: str,
    items: list[dict],
    allow_mismatch: bool = False,
) -> dict:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM datasets WHERE id=?", (dataset_id,)) as cur:
            row = await cur.fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Dataset not found")
        ds = dict(row)

        # Study-binding check (consideration #2): warn on UID mismatch for DICOM items
        bound_uid = ds.get("study_instance_uid")
        for item in items:
            if item.get("kind") == "dicom_series" and bound_uid:
                incoming_uid = item.get("study_instance_uid")
                if incoming_uid and incoming_uid != bound_uid and not allow_mismatch:
                    raise HTTPException(
                        status_code=409,
                        detail=(
                            f"StudyInstanceUID mismatch: dataset is bound to {bound_uid!r}, "
                            f"incoming item has {incoming_uid!r}. "
                            "Set allow_mismatch=true to override."
                        ),
                    )

        now = datetime.now(UTC).isoformat()
        await _insert_items(db, dataset_id, items, now)

        # Update kind + updated_at
        new_kind = _infer_kind(
            [{"kind": ds["kind"]}] + items if ds["kind"] in ("cdms", "dicom") else items
        )
        # Re-infer properly from all items
        async with db.execute(
            "SELECT kind FROM dataset_items WHERE dataset_id=?", (dataset_id,)
        ) as cur:
            all_kinds = [r[0] for r in await cur.fetchall()]
        has_dicom = "dicom_series" in all_kinds
        has_cdms = "cdms_group" in all_kinds
        new_kind = "mixed" if (has_dicom and has_cdms) else ("cdms" if has_cdms else "dicom")

        await db.execute(
            "UPDATE datasets SET kind=?, updated_at=? WHERE id=?",
            (new_kind, now, dataset_id),
        )
        await db.commit()
        result = await _build_dataset_response(db, dataset_id)
    return result


async def _insert_items(
    db: aiosqlite.Connection, dataset_id: str, items: list[dict], now: str
) -> None:
    """Insert items; DICOM series are deduped by SeriesInstanceUID (merge file lists)."""
    for item in items:
        kind = item.get("kind", "dicom_series")
        file_paths = item.get("file_paths", [])
        file_paths_json = json.dumps(file_paths)
        item_count = item.get("item_count") or len(file_paths) or item.get("instance_count") or 0

        if kind == "dicom_series":
            series_uid = item.get("series_instance_uid")
            if series_uid:
                # Consideration #1: dedupe — find existing item with same UID
                async with db.execute(
                    "SELECT id, file_paths_json, item_count FROM dataset_items "
                    "WHERE dataset_id=? AND series_instance_uid=?",
                    (dataset_id, series_uid),
                ) as cur:
                    existing = await cur.fetchone()
                if existing:
                    # Merge file lists, update count and source path
                    existing_paths: list[str] = json.loads(existing[1] or "[]")
                    merged = list(dict.fromkeys(existing_paths + file_paths))
                    await db.execute(
                        "UPDATE dataset_items SET file_paths_json=?, item_count=?, source_path=? "
                        "WHERE id=?",
                        (json.dumps(merged), len(merged), item["source_path"], existing[0]),
                    )
                    await db.commit()
                    continue  # skip INSERT

            item_id = str(uuid.uuid4())
            await db.execute(
                """INSERT INTO dataset_items
                   (id, dataset_id, kind, modality, series_description,
                    series_instance_uid, series_number, frame_of_reference_uid,
                    sop_class_uid, instance_count, source_path, file_paths_json,
                    item_count, added_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    item_id,
                    dataset_id,
                    "dicom_series",
                    item.get("modality"),
                    item.get("series_description"),
                    series_uid,
                    item.get("series_number"),
                    item.get("frame_of_reference_uid"),
                    item.get("sop_class_uid"),
                    item.get("instance_count"),
                    item["source_path"],
                    file_paths_json,
                    item_count,
                    now,
                ),
            )
        else:  # cdms_group
            item_id = str(uuid.uuid4())
            await db.execute(
                """INSERT INTO dataset_items
                   (id, dataset_id, kind, type_code, type_name, fraction, machine,
                    source_path, file_paths_json, item_count, added_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    item_id,
                    dataset_id,
                    "cdms_group",
                    item.get("type_code"),
                    item.get("type_name"),
                    item.get("fraction"),
                    item.get("machine"),
                    item["source_path"],
                    file_paths_json,
                    item_count,
                    now,
                ),
            )
    await db.commit()


async def remove_dataset_item(item_id: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT dataset_id FROM dataset_items WHERE id=?", (item_id,)) as cur:
            row = await cur.fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Item not found")
        await db.execute("DELETE FROM dataset_items WHERE id=?", (item_id,))
        # Re-infer kind on parent dataset
        now = datetime.now(UTC).isoformat()
        dataset_id = row[0]
        async with db.execute(
            "SELECT kind FROM dataset_items WHERE dataset_id=?", (dataset_id,)
        ) as cur:
            all_kinds = [r[0] for r in await cur.fetchall()]
        has_dicom = "dicom_series" in all_kinds
        has_cdms = "cdms_group" in all_kinds
        new_kind = "mixed" if (has_dicom and has_cdms) else ("cdms" if has_cdms else "dicom")
        await db.execute(
            "UPDATE datasets SET kind=?, updated_at=? WHERE id=?",
            (new_kind, now, dataset_id),
        )
        await db.commit()


async def delete_dataset(dataset_id: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT id FROM datasets WHERE id=?", (dataset_id,)) as cur:
            if await cur.fetchone() is None:
                raise HTTPException(status_code=404, detail="Dataset not found")
        # ON DELETE CASCADE removes items
        await db.execute("DELETE FROM datasets WHERE id=?", (dataset_id,))
        await db.commit()


async def mount_dataset(session_id: str, patient_data_path: str) -> dict:
    """Legacy shim: create a single-item 'unknown' dataset from a raw path.

    Kept for back-compat with any external callers. Creates a dataset with
    a single CDMS or DICOM item depending on what's found at the path.
    """
    path = Path(patient_data_path)
    if not path.exists():
        raise HTTPException(status_code=422, detail=f"Path does not exist: {patient_data_path}")
    label = path.name or str(path)
    item: dict = {
        "kind": "dicom_series",
        "source_path": str(path),
        "file_paths": [],
        "item_count": 0,
    }
    if scan_folder is not None:
        scanned = scan_folder(str(path))
        if scanned:
            item["content_type"] = scanned[0].content.kind
    item.setdefault("content_type", "dicom_series")
    return await create_dataset(session_id, label, [item], content_type=item["content_type"])
