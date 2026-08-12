"""Export the FastAPI contract as a versioned OpenAPI document."""

import argparse
import json
from pathlib import Path


def export_openapi(output: Path) -> None:
    from main import app

    document = app.openapi()
    document["openapi"] = "3.1.0"
    document.setdefault("info", {})["version"] = "1.0.0"
    output.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "output",
        type=Path,
        default=Path("openapi-v1.json"),
        help="Output path (default: openapi-v1.json)",
    )
    args = parser.parse_args()
    export_openapi(args.output)


if __name__ == "__main__":
    main()
