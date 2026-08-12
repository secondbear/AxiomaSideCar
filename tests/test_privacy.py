import json

import config
from services.privacy import sanitize_dataset, sanitize_patient


def test_privacy_redaction_removes_identifiers_and_paths(monkeypatch):
    monkeypatch.setattr(config.settings, "privacy_redaction_enabled", True)
    patient = sanitize_patient(
        {"id": "p1", "name": "Ada", "dob": "1900-01-01", "external_id": "MRN"}
    )
    dataset = sanitize_dataset(
        {
            "path": "/data/case",
            "patient_name": "Ada",
            "patient_id": "p1",
            "items": [
                {"source_path": "/data/case", "file_paths_json": json.dumps(["/data/case/a.dcm"])}
            ],
        }
    )

    assert patient["name"] is None
    assert patient["dob"] is None
    assert patient["external_id"] is None
    assert dataset["path"] == ""
    assert dataset["items"][0]["source_path"] == ""
    assert dataset["items"][0]["file_paths_json"] == "[]"
