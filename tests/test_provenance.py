from services.provenance import normalize_provenance


def test_normalize_provenance_wraps_engine_payload(monkeypatch):
    monkeypatch.setenv("AXIOMA_GIT_SHA", "abc123")

    manifest = normalize_provenance("session-1", {"beamModel": "test"})

    assert manifest["schemaVersion"] == 1
    assert manifest["sessionId"] == "session-1"
    assert manifest["gitSha"] == "abc123"
    assert manifest["engine"] == {"beamModel": "test"}
