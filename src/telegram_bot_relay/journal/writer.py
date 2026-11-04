"""Single-writer durable journal with recoverable directory rotation."""

import fcntl
import os
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path

from pydantic import JsonValue

from telegram_bot_relay.journal.models import Record
from telegram_bot_relay.journal.reader import read_records
from telegram_bot_relay.journal.rotation import Schedule


def utcnow() -> datetime:
    return datetime.now(UTC)


def sync_directory(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def private_path(path: Path) -> None:
    if path.stat().st_mode & 0o077:
        raise ValueError("Archive paths must not grant group or other permissions")


def private_directory(path: Path) -> None:
    """Validate a derived directory and persist its parent's directory entry."""
    if path.is_symlink():
        raise ValueError("Symlink directory is not supported")
    private_path(path)
    sync_directory(path.parent)


def backup_tail(path: Path, offset: int, tail: bytes) -> None:
    backup = path.with_name(f"incomplete-tail-{offset}-{sha256(tail).hexdigest()}.bin")
    temporary = backup.with_suffix(".tmp")
    descriptor = os.open(temporary, os.O_CREAT | os.O_TRUNC | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "wb") as saved:
        os.fchmod(saved.fileno(), 0o600)
        saved.write(tail)
        saved.flush()
        os.fsync(saved.fileno())
    os.replace(temporary, backup)
    sync_directory(path.parent)


class Journal:
    def __init__(
        self,
        root: Path,
        rotate: str = "0 0 * * *",
        timezone: str = "UTC",
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        private_path(self.root)
        self.clock = clock
        descriptor = os.open(
            self.root / ".writer.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600
        )
        self.lock = os.fdopen(descriptor, "a")
        try:
            private_path(self.root / ".writer.lock")
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.seq = 0
            self.failed = False
            self.schedule = Schedule(rotate, timezone, self.clock())
            self._recover()
        except BaseException:
            self.lock.close()
            raise

    def _recover(self) -> None:
        paths = sorted(self.root.glob("*/events.jsonl"))
        recovered: str | None = None
        for path in paths:
            if path.is_symlink() or path.parent.is_symlink():
                raise ValueError("Symlink archive segments are not supported")
            private_path(path.parent)
            private_path(path)
        for index, path in enumerate(paths):
            with path.open("r+b") as stream:
                valid_end = 0
                for line in stream:
                    if not line.endswith(b"\n"):
                        if index != len(paths) - 1:
                            raise ValueError("Partial line in closed segment")
                        recovered = str(path.relative_to(self.root))
                        backup_tail(path, valid_end, line)
                        stream.seek(valid_end)
                        stream.truncate()
                        stream.flush()
                        os.fsync(stream.fileno())
                        sync_directory(path.parent)
                        break
                    record = Record.model_validate_json(line)
                    if record.seq != self.seq + 1 or record.period != path.parent.name:
                        raise ValueError("Journal sequence/period mismatch")
                    self.seq = record.seq
                    valid_end += len(line)
        self.period = paths[-1].parent.name if paths else self._name()
        directory = self.root / self.period
        directory.mkdir(exist_ok=True, mode=0o700)
        private_path(directory)
        (directory / "events.jsonl").touch(mode=0o600, exist_ok=True)
        sync_directory(directory)
        sync_directory(self.root)
        records = self.records()
        last = None
        for record in records:
            last = record
        if last is not None and last.type == "rotate":
            target = last.data["next_events"]
            if not isinstance(target, str):
                raise ValueError("Invalid rotation link")
            parts = Path(target).parts
            if (
                len(parts) != 3
                or parts[0] != ".."
                or parts[2] != "events.jsonl"
                or target != f"../{parts[1]}/events.jsonl"
                or parts[1] <= last.period
            ):
                raise ValueError("Invalid rotation path")
            new_path = self.root / parts[1] / "events.jsonl"
            if new_path.parent.is_symlink() or new_path.is_symlink():
                raise ValueError("Symlink rotation targets are not supported")
            self.period = new_path.parent.name
            new_path.parent.mkdir(exist_ok=True, mode=0o700)
            private_path(new_path.parent)
            new_path.touch(mode=0o600, exist_ok=True)
            self._append("segment_started", {"prev_events": f"../{last.period}/events.jsonl"})
        elif last is None or last.period != self.period:
            start_data: dict[str, JsonValue] = {}
            if last is not None:
                old = last.period
                self.period = old
                self._append("rotate", {"next_events": f"../{directory.name}/events.jsonl"})
                self.period = directory.name
                start_data = {"prev_events": f"../{old}/events.jsonl"}
            self._append("segment_started", start_data)
        for previous, following in zip(paths, paths[1:], strict=False):
            with previous.open("rb") as f:
                lines = f.readlines()
            terminal = Record.model_validate_json(lines[-1]) if lines else None
            if terminal is None or terminal.type != "rotate":
                raise ValueError("Closed segment lacks continuation")
            if (previous.parent / str(terminal.data["next_events"])).resolve() != following:
                raise ValueError("Broken continuation chain")
        if recovered:
            self._append("tail_recovered", {"path": recovered})

    def _name(self) -> str:
        return f"{self.seq + 1:020d}-{self.clock().strftime('%Y%m%dT%H%M%S%fZ')}"

    def _append(self, kind: str, data: dict[str, JsonValue]) -> Record:
        if self.failed:
            raise OSError("Journal stopped after write failure")
        value = Record.model_validate(
            {
                "seq": self.seq + 1,
                "timestamp": self.clock().isoformat(),
                "period": self.period,
                "type": kind,
                "data": data,
            }
        )
        try:
            with (self.root / self.period / "events.jsonl").open("ab") as stream:
                stream.write(value.model_dump_json().encode() + b"\n")
                stream.flush()
                os.fsync(stream.fileno())
        except OSError:
            self.failed = True
            raise
        self.seq += 1
        return value

    def append(self, kind: str, data: dict[str, JsonValue]) -> Record:
        if self.schedule.due(self.clock()):
            self.rotate()
        return self._append(kind, data)

    def rotate(self) -> None:
        old = self.period
        new = self._name()
        directory = self.root / new
        directory.mkdir(mode=0o700)
        (directory / "events.jsonl").touch(mode=0o600)
        sync_directory(directory)
        sync_directory(self.root)
        self._append("rotate", {"next_events": f"../{new}/events.jsonl"})
        self.period = new
        self._append("segment_started", {"prev_events": f"../{old}/events.jsonl"})

    def records(self, after: int = 0) -> Iterator[Record]:
        if after >= self.seq:
            return iter(())
        return read_records(self.root, after)

    def find(self, seq: int) -> Record:
        for record in self.records(seq - 1):
            if record.seq == seq:
                return record
        raise ValueError("Record not found")

    def close(self) -> None:
        self.lock.close()
