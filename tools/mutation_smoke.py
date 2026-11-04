"""Prove selected safety tests fail when critical invariants are removed."""

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    mutations = [
        (
            "application/operations.py",
            "if not allowed:",
            "if False:",
            "tests/test_operations.py::test_outbound_audit_statuses[denied]",
        ),
        (
            "service.py",
            "record = self.journal.append(\n",
            "self.offset = update_id + 1 if isinstance(update_id, int) else self.offset\n        record = self.journal.append(\n",
            "tests/test_service.py::test_all_input_archived_before_offset_and_recovered",
        ),
        (
            "journal/writer.py",
            "os.fsync(stream.fileno())",
            "pass",
            "tests/test_journal.py::test_disk_failure_stops_writer",
        ),
    ]
    for relative, before, after, test in mutations:
        with tempfile.TemporaryDirectory(prefix="relay-mutant-") as directory:
            work = Path(directory)
            shutil.copytree(root / "src", work / "src")
            shutil.copytree(root / "tests", work / "tests")
            shutil.copyfile(root / "pyproject.toml", work / "pyproject.toml")
            path = work / "src/telegram_bot_relay" / relative
            original = path.read_text()
            if before not in original:
                raise RuntimeError(f"Mutation location changed: {relative}")
            environment = {**os.environ, "PYTHONPATH": str(work / "src")}
            command = [sys.executable, "-m", "pytest", "--no-cov", "-q", test]
            baseline = subprocess.run(
                command, cwd=work, env=environment, capture_output=True, timeout=30
            )
            if baseline.returncode != 0:
                raise RuntimeError("Mutation baseline failed: " + baseline.stdout.decode())
            path.write_text(original.replace(before, after))
            mutant = subprocess.run(
                command, cwd=work, env=environment, capture_output=True, timeout=30
            )
            if mutant.returncode != 1 or b"FAILED" not in mutant.stdout:
                raise RuntimeError("Mutant survived or failed to run: " + mutant.stdout.decode())
            print(f"Detected invariant mutation: {relative}", flush=True)


if __name__ == "__main__":
    main()
