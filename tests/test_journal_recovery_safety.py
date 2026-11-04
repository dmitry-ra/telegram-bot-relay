from pathlib import Path

import pytest

from telegram_bot_relay.journal import writer
from telegram_bot_relay.journal.writer import Journal


@pytest.mark.parametrize("target", ["root", "segment", "events", "lock"])
def test_existing_permissive_archive_paths_fail_closed(tmp_path: Path, target: str) -> None:
    journal = Journal(tmp_path)
    segment = tmp_path / journal.period
    events = segment / "events.jsonl"
    journal.close()
    path = {
        "root": tmp_path,
        "segment": segment,
        "events": events,
        "lock": tmp_path / ".writer.lock",
    }[target]
    before = events.read_bytes()
    path.chmod(0o755 if target in ("root", "segment") else 0o644)
    with pytest.raises(ValueError, match="permissions"):
        Journal(tmp_path)
    assert events.read_bytes() == before


def test_recovery_backup_is_durable_idempotent_and_preserves_other_tails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    journal = Journal(tmp_path)
    path = tmp_path / journal.period / "events.jsonl"
    journal.close()
    valid = path.read_bytes()
    first_tail = b'{"partial":'
    path.write_bytes(valid + first_tail)
    real_sync = writer.sync_directory
    interrupted = False

    def crash_before_truncate(directory: Path) -> None:
        nonlocal interrupted
        real_sync(directory)
        if not interrupted and list(directory.glob("incomplete-tail-*.bin")):
            interrupted = True
            assert path.read_bytes() == valid + first_tail
            raise OSError("simulated crash after durable backup")

    with monkeypatch.context() as scoped:
        scoped.setattr(writer, "sync_directory", crash_before_truncate)
        with pytest.raises(OSError, match="simulated crash"):
            Journal(tmp_path)
    backups = list(path.parent.glob("incomplete-tail-*.bin"))
    assert interrupted and len(backups) == 1
    assert backups[0].read_bytes() == first_tail
    journal = Journal(tmp_path)
    assert list(journal.records())[-1].type == "tail_recovered"
    journal.close()
    assert list(path.parent.glob("incomplete-tail-*.bin")) == backups
    assert backups[0].read_bytes() == first_tail
    second_tail = b'{"different":'
    with path.open("ab") as stream:
        stream.write(second_tail)
    journal = Journal(tmp_path)
    journal.close()
    assert {p.read_bytes() for p in path.parent.glob("incomplete-tail-*.bin")} == {
        first_tail,
        second_tail,
    }
    assert all(p.stat().st_mode & 0o077 == 0 for p in path.parent.glob("incomplete-tail-*.bin"))
    assert not list(path.parent.glob("incomplete-tail-*.tmp"))


@pytest.mark.parametrize("target", ["basename", "current", "closed", "symlink"])
def test_recovery_rejects_invalid_rotation_targets(tmp_path: Path, target: str) -> None:
    journal = Journal(tmp_path)
    first = journal.period
    if target == "closed":
        journal.rotate()
    current = journal.period
    next_name = "99999999999999999999-next"
    if target == "symlink":
        outside = tmp_path.parent / (tmp_path.name + "-outside")
        outside.mkdir(mode=0o700)
        (tmp_path / next_name).symlink_to(outside, target_is_directory=True)
    destinations = {
        "basename": f"../{next_name}/other.bin",
        "current": f"../{current}/events.jsonl",
        "closed": f"../{first}/events.jsonl",
        "symlink": f"../{next_name}/events.jsonl",
    }
    journal._append("rotate", {"next_events": destinations[target]})
    before = (tmp_path / current / "events.jsonl").read_bytes()
    journal.close()
    with pytest.raises(ValueError, match="rotation"):
        Journal(tmp_path)
    assert (tmp_path / current / "events.jsonl").read_bytes() == before
    assert not (tmp_path / next_name / "other.bin").exists()
    if target == "symlink":
        assert not (outside / "events.jsonl").exists()
