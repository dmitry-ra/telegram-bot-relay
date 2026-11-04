"""Readers follow complete JSON lines and continuation links."""

from collections.abc import Iterator
from pathlib import Path

from telegram_bot_relay.journal.models import Record


def read_records(root: Path, after: int = 0) -> Iterator[Record]:
    paths = sorted(root.glob("*/events.jsonl"))
    for index, path in enumerate(paths):
        if index + 1 < len(paths) and int(paths[index + 1].parent.name.split("-", 1)[0]) <= after:
            continue
        with path.open("rb") as stream:
            for line in stream:
                if not line.endswith(b"\n"):
                    break
                record = Record.model_validate_json(line)
                if record.seq > after:
                    yield record


def continuation(path: Path) -> Path | None:
    last = None
    with path.open("rb") as stream:
        for line in stream:
            if not line.endswith(b"\n"):
                break
            last = Record.model_validate_json(line)
    if last is not None and last.type == "rotate":
        target = last.data.get("next_events")
        if not isinstance(target, str):
            raise ValueError("Invalid continuation")
        resolved = (path.parent / target).resolve()
        if not resolved.is_relative_to(path.parent.parent.resolve()):
            raise ValueError("Continuation escapes archive")
        return resolved
    return None
