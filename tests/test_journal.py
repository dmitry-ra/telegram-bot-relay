import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest
from jsonschema import Draft202012Validator

from telegram_bot_relay.journal.models import Record
from telegram_bot_relay.journal.reader import continuation
from telegram_bot_relay.journal.writer import Journal


def test_rotation_reader_and_recovery(tmp_path: Path) -> None:
    now = datetime(2026, 11, 1, 23, 59, tzinfo=UTC)
    journal = Journal(tmp_path, clock=lambda: now)
    first = journal.append(
        "message_received", {"original": {"text": "\u041f\u0440\u0438\u0432\u0435\u0442"}}
    )
    old = tmp_path / first.period / "events.jsonl"
    now += timedelta(minutes=2)
    second = journal.append("message_received", {"original": {"text": "next"}})
    new = tmp_path / second.period / "events.jsonl"
    assert continuation(old) == new
    assert continuation(new) is None
    records = list(journal.records())
    assert [r.seq for r in records] == list(range(1, 6))
    validator = Draft202012Validator(Record.model_json_schema())
    for record in records:
        validator.validate(record.model_dump(mode="json"))
    with pytest.raises(BlockingIOError):
        Journal(tmp_path)
    journal.close()
    with new.open("ab") as stream:
        stream.write(b'{"partial":')
    journal = Journal(tmp_path, clock=lambda: now)
    assert list(journal.records())[-1].type == "tail_recovered"
    assert (new.parent / "incomplete-tail.bin").read_bytes() == b'{"partial":'
    journal.close()


def test_rotation_crash_after_link(tmp_path: Path) -> None:
    journal = Journal(tmp_path)
    old = journal.period
    next_dir = "00000000000000000003-next"
    (tmp_path / next_dir).mkdir()
    (tmp_path / next_dir / "events.jsonl").touch()
    journal._append("rotate", {"next_events": f"../{next_dir}/events.jsonl"})
    journal.close()
    journal = Journal(tmp_path)
    assert journal.period == next_dir
    assert list(journal.records())[-1].data["prev_events"] == f"../{old}/events.jsonl"
    journal.close()


def test_rotation_crash_before_link(tmp_path: Path) -> None:
    journal = Journal(tmp_path)
    next_dir = tmp_path / "00000000000000000003-next"
    next_dir.mkdir()
    (next_dir / "events.jsonl").touch()
    old = journal.root / journal.period / "events.jsonl"
    journal.close()
    journal = Journal(tmp_path)
    assert continuation(old) == next_dir / "events.jsonl"
    assert list(journal.records())[-1].data["prev_events"] == f"../{old.parent.name}/events.jsonl"
    journal.close()


def test_disk_failure_stops_writer(tmp_path: Path) -> None:
    journal = Journal(tmp_path)
    with patch("os.fsync", side_effect=OSError("disk full")):
        with pytest.raises(OSError):
            journal.append("message_received", {})
    with pytest.raises(OSError, match="stopped"):
        journal.append("message_received", {})
    journal.close()


def test_complete_corruption_is_not_silently_repaired(tmp_path: Path) -> None:
    journal = Journal(tmp_path)
    path = tmp_path / journal.period / "events.jsonl"
    journal.close()
    with path.open("ab") as stream:
        stream.write(b"not-json\n")
    with pytest.raises(ValueError):
        Journal(tmp_path)


def test_sequence_and_closed_tail_corruption(tmp_path: Path) -> None:
    journal = Journal(tmp_path)
    old = tmp_path / journal.period / "events.jsonl"
    journal.rotate()
    journal.close()
    with old.open("ab") as stream:
        stream.write(b"partial")
    with pytest.raises(ValueError, match="Partial line"):
        Journal(tmp_path)


def test_archive_permissions_and_find(tmp_path: Path) -> None:
    journal = Journal(tmp_path)
    record = journal.append("message_received", {})
    assert journal.find(record.seq) == record
    with pytest.raises(ValueError, match="not found"):
        journal.find(999)
    assert os.stat(tmp_path / journal.period / "events.jsonl").st_mode & 0o077 == 0
    journal.close()
