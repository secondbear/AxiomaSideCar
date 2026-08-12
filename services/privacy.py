"""Response privacy controls for research and clinical-data deployments."""

import json
from typing import Any

from config import settings


def _redact(value: Any) -> Any:
    return None if settings.privacy_redaction_enabled else value


def sanitize_patient(patient: dict) -> dict:
    if not settings.privacy_redaction_enabled:
        return patient
    redacted = dict(patient)
    redacted.update(name=None, dob=None, external_id=None)
    return redacted


def sanitize_dataset(dataset: dict) -> dict:
    if not settings.privacy_redaction_enabled:
        return dataset
    redacted = dict(dataset)
    redacted.update(patient_name=None, patient_id=None, path="")
    items = []
    for item in dataset.get("items", []):
        safe_item = dict(item)
        safe_item.update(source_path="", file_paths_json=json.dumps([]))
        items.append(safe_item)
    redacted["items"] = items
    return redacted
