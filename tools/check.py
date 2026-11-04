"""Run the same checks locally and in CI."""

import subprocess
from pathlib import Path


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    names = subprocess.check_output(
        ["git", "ls-files", "-co", "--exclude-standard", "-z"], cwd=root
    )
    for name in set(names.decode().split("\0")) - {""}:
        name.encode("ascii")
        (root / name).read_bytes().decode("ascii")
    for command in [
        ["black", "--check", "."],
        ["ruff", "check", "."],
        ["mypy", "src", "tools", "tests"],
        ["python", "-m", "compileall", "-q", "src", "tools", "tests"],
        ["python", "tools/export_schemas.py", "--check"],
        ["pytest"],
    ]:
        subprocess.run(command, cwd=root, check=True)


if __name__ == "__main__":
    main()
