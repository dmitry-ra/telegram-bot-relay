"""Generate or check packaged schemas."""

import argparse
import json
from pathlib import Path

from telegram_bot_relay.interfaces.specification import openapi
from telegram_bot_relay.journal.models import Record


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1] / "src/telegram_bot_relay"
    for relative, value in [
        ("interfaces/openapi.json", openapi()),
        ("schemas/journal.schema.json", Record.model_json_schema()),
    ]:
        path = root / relative
        expected = json.dumps(value, indent=2, ensure_ascii=True) + "\n"
        if args.check:
            if path.read_text() != expected:
                raise SystemExit(f"Schema drift: {relative}")
        else:
            path.parent.mkdir(exist_ok=True)
            path.write_text(expected)


if __name__ == "__main__":
    main()
