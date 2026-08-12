import json
from pathlib import Path

from scripts.export_openapi import export_openapi

ROOT = Path(__file__).parents[1]


def test_committed_openapi_is_current(tmp_path):
    generated = tmp_path / "openapi-v1.json"
    export_openapi(generated)

    committed = json.loads((ROOT / "openapi-v1.json").read_text())
    assert json.loads(generated.read_text()) == committed
