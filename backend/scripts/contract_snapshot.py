"""Update/check API contracts with only the project's locked runtime dependencies."""
import argparse
import json
from pathlib import Path
import sys

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
from app.main import app

SNAPSHOT = BACKEND / "app/tests/snapshots/openapi.json"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    current = json.dumps(app.openapi(), ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    if args.check:
        if SNAPSHOT.read_text(encoding="utf-8") != current:
            raise SystemExit("API contract changed; update frontend types and regenerate the snapshot")
    else:
        SNAPSHOT.parent.mkdir(parents=True, exist_ok=True)
        SNAPSHOT.write_text(current, encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()
