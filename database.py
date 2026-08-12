import aiosqlite

from config import settings

DB_PATH = settings.db_path

CREATE_TABLES = """
CREATE TABLE IF NOT EXISTS patients (
    id          TEXT PRIMARY KEY,
    external_id TEXT,           -- MRN or CDMS patient ID
    name        TEXT,
    dob         TEXT,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    id          TEXT PRIMARY KEY,
    patient_id  TEXT NOT NULL REFERENCES patients(id),
    label       TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS datasets (
    id                   TEXT PRIMARY KEY,
    session_id           TEXT NOT NULL REFERENCES sessions(id),
    label                TEXT NOT NULL,          -- user-visible name
    kind                 TEXT NOT NULL DEFAULT 'dicom', -- 'dicom' | 'cdms' | 'mixed'
    -- study-level identity (set from first DICOM item; NULL for CDMS-only)
    patient_name         TEXT,
    patient_id           TEXT,
    study_instance_uid   TEXT,
    frame_of_reference_uid TEXT,
    content_type          TEXT NOT NULL DEFAULT '',
    -- legacy compat: path of first source (kept for slice endpoint)
    path                 TEXT NOT NULL DEFAULT '',
    created_at           TEXT NOT NULL,
    updated_at           TEXT NOT NULL
);

-- One row per series (DICOM) or fraction/type-group (CDMS).
CREATE TABLE IF NOT EXISTS dataset_items (
    id                   TEXT PRIMARY KEY,
    dataset_id           TEXT NOT NULL REFERENCES datasets(id) ON DELETE CASCADE,
    kind                 TEXT NOT NULL,  -- 'dicom_series' | 'cdms_group'
    -- DICOM series fields
    modality             TEXT,
    series_description   TEXT,
    series_instance_uid  TEXT,           -- dedupe key for DICOM
    series_number        INTEGER,
    frame_of_reference_uid TEXT,
    sop_class_uid        TEXT,
    instance_count       INTEGER,
    -- CDMS group fields
    type_code            TEXT,           -- e.g. '99B0', '01B0'
    type_name            TEXT,           -- human name from TYPE_DESCRIPTIONS
    fraction             TEXT,           -- fraction/session key from group_by_fraction
    machine              TEXT,
    -- shared
    source_path          TEXT NOT NULL,  -- directory containing this item's files
    file_paths_json      TEXT,           -- JSON array of absolute file paths
    item_count           INTEGER NOT NULL DEFAULT 0,
    added_at             TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS jobs (
    id          TEXT PRIMARY KEY,
    session_id  TEXT NOT NULL,
    type        TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'queued',
    progress    REAL NOT NULL DEFAULT 0.0,
    message     TEXT,
    params      TEXT,           -- JSON blob
    result      TEXT,           -- JSON blob set on completion
    cancel_requested INTEGER NOT NULL DEFAULT 0,
    started_at  TEXT,
    finished_at TEXT,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS job_artifacts (
    id          TEXT PRIMARY KEY,
    job_id      TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    path        TEXT NOT NULL,
    media_type  TEXT,
    size_bytes  INTEGER,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS machines (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    engine      TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'draft',
    params      TEXT NOT NULL,  -- JSON: {mu, sigmaP, sigmaW, tBase}
    locked_hash TEXT,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS contour_reviews (
    id              TEXT PRIMARY KEY,
    session_id      TEXT NOT NULL,
    fraction_index  INTEGER NOT NULL,
    structure_id    TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'pending'  -- pending|accepted|rejected
);
"""


async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript(CREATE_TABLES)
        await db.commit()
        # ── Migrations ────────────────────────────────────────────────────────
        # Add columns introduced in the dataset redesign (idempotent ALTERs).
        for col, defn in [
            ("label", "TEXT NOT NULL DEFAULT ''"),
            ("kind", "TEXT NOT NULL DEFAULT 'dicom'"),
            ("patient_name", "TEXT"),
            ("patient_id", "TEXT"),
            ("study_instance_uid", "TEXT"),
            ("frame_of_reference_uid", "TEXT"),
            ("content_type", "TEXT NOT NULL DEFAULT ''"),
            ("updated_at", "TEXT NOT NULL DEFAULT ''"),
        ]:
            try:
                await db.execute(f"ALTER TABLE datasets ADD COLUMN {col} {defn}")
                await db.commit()
            except Exception:
                pass  # column already exists
        for col, defn in [
            ("cancel_requested", "INTEGER NOT NULL DEFAULT 0"),
            ("started_at", "TEXT"),
            ("finished_at", "TEXT"),
        ]:
            try:
                await db.execute(f"ALTER TABLE jobs ADD COLUMN {col} {defn}")
                await db.commit()
            except Exception:
                pass  # column already exists
        # Ensure dataset_items table exists (CREATE TABLE IF NOT EXISTS already handles new DBs)
        await db.commit()
