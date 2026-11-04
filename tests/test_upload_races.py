import os
from pathlib import Path

import pytest

from telegram_bot_relay.application.operations import copy_upload


@pytest.mark.parametrize("target", ["file", "directory", "root_ancestor"])
def test_upload_rejects_swapped_symlinks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, target: str
) -> None:
    base = tmp_path / "base"
    root = base / "uploads"
    directory = root / "nested"
    directory.mkdir(parents=True)
    source = directory / "file"
    source.write_bytes(b"allowed")
    outside = tmp_path / "outside"
    (outside / "uploads/nested").mkdir(parents=True)
    (outside / "uploads/nested/file").write_bytes(b"private")
    destination = tmp_path / "snapshot"
    real_open = os.open
    swapped = False

    def racing_open(
        path: str | Path, flags: int, mode: int = 0o777, *, dir_fd: int | None = None
    ) -> int:
        nonlocal swapped
        trigger = {"file": "file", "directory": "nested", "root_ancestor": "base"}[target]
        if not swapped and dir_fd is not None and str(path) == trigger:
            swapped = True
            victim = {"file": source, "directory": directory, "root_ancestor": base}[target]
            replacement = {
                "file": outside / "uploads/nested/file",
                "directory": outside / "uploads/nested",
                "root_ancestor": outside,
            }[target]
            victim.rename(victim.with_name(victim.name + "-saved"))
            victim.symlink_to(replacement, target_is_directory=target != "file")
        return real_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(os, "open", racing_open)
    with pytest.raises(OSError):
        copy_upload(source, root, destination, 100)
    assert swapped
    assert not destination.exists()


def test_upload_rejects_growth_after_stat(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "source"
    source.write_bytes(b"small")
    destination = tmp_path / "snapshot"
    real_stat = os.fstat

    def racing_stat(fd: int) -> os.stat_result:
        result = real_stat(fd)
        source.write_bytes(b"x" * 101)
        return result

    monkeypatch.setattr(os, "fstat", racing_stat)
    with pytest.raises(ValueError, match="size limit"):
        copy_upload(source, tmp_path, destination, 100)
    assert not destination.exists()


def test_upload_rejects_nonregular_file(tmp_path: Path) -> None:
    source = tmp_path / "pipe"
    os.mkfifo(source)
    with pytest.raises(ValueError, match="file type"):
        copy_upload(source, tmp_path, tmp_path / "snapshot", 100)


def test_upload_preserves_existing_destination(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.write_bytes(b"upload")
    destination = tmp_path / "existing"
    destination.write_bytes(b"retained")
    with pytest.raises(FileExistsError):
        copy_upload(source, tmp_path, destination, 100)
    assert destination.read_bytes() == b"retained"
