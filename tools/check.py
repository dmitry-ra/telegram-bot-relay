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
        data = (root / name).read_bytes()
        signatures = {
            "docs/assets/relay-demo.gif": b"GIF89a",
            "docs/assets/relay-demo.png": b"\x89PNG\r\n\x1a\n",
        }
        if name in signatures:
            if not data.startswith(signatures[name]):
                raise ValueError("Invalid documentation image: " + name)
        else:
            data.decode("ascii")
    for command in [
        ["black", "--check", "."],
        ["ruff", "check", "."],
        ["mypy", "src", "tools", "tests"],
        ["python", "-m", "compileall", "-q", "src", "tools", "tests"],
        ["python", "tools/export_schemas.py", "--check"],
        ["python", "tools/demo.py", "--pace", "0"],
        ["pytest"],
    ]:
        subprocess.run(command, cwd=root, check=True)


if __name__ == "__main__":
    main()
