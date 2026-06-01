import aiosqlite

DB_PATH = "axioma.db"

CREATE_TABLES = """
CREATE TABLE IF NOT EXISTS jobs (
    id          TEXT PRIMARY KEY,
    session_id  TEXT NOT NULL,
    type        TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'queued',
    progress    REAL NOT NULL DEFAULT 0.0,
    message     TEXT,
    params      TEXT,           -- JSON blob
    result      TEXT,           -- JSON blob set on completion
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
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
