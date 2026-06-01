"""Application settings loaded from environment variables / .env file.

All other modules should import ``settings`` from here rather than reading
os.environ directly.  Add a key here when you need a new config value — don't
scatter os.getenv() calls around the codebase.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # SQLite database file path (relative to CWD or absolute)
    db_path: str = "axioma.db"

    # Allowed CORS origin for the Tauri/React frontend dev server
    cors_origin: str = "http://localhost:1420"

    # Optional root directory for patient data and engine output artefacts.
    # When set, relative paths in job params are resolved against this root.
    engine_data_root: str | None = None


settings = Settings()
