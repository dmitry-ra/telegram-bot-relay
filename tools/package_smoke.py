"""Install wheel and sdist outside the checkout and verify packaged resources."""

import subprocess
import tarfile
import tempfile
import tomllib
from pathlib import Path


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    version = tomllib.loads((root / "pyproject.toml").read_text())["project"]["version"]
    artifacts = [
        next((root / "dist").glob(f"telegram_bot_relay-{version}-*.whl")),
        root / "dist" / f"telegram_bot_relay-{version}.tar.gz",
    ]
    with tarfile.open(artifacts[1]) as archive:
        assert any(n.endswith("skills/telegram-message/SKILL.md") for n in archive.getnames())
    for artifact in artifacts:
        with tempfile.TemporaryDirectory(prefix="relay-package-") as directory:
            work = Path(directory)
            subprocess.run(["uv", "venv", str(work / "env")], check=True)
            python = work / "env/bin/python"
            subprocess.run(
                ["uv", "pip", "install", "--python", str(python), str(artifact)], check=True
            )
            result = subprocess.run(
                [str(python), "-m", "telegram_bot_relay.cli", "--version"],
                cwd=work,
                check=True,
                text=True,
                capture_output=True,
            )
            assert result.stdout.strip() == version
            subprocess.run(
                [
                    str(python),
                    "-c",
                    "from importlib.resources import files; import json; p=files('telegram_bot_relay'); assert json.loads(p.joinpath('interfaces/openapi.json').read_text())['openapi']=='3.1.0'; assert json.loads(p.joinpath('schemas/journal.schema.json').read_text())['type']=='object'; assert 'telegram-message' in p.joinpath('skills/telegram-message/SKILL.md').read_text()",
                ],
                cwd=work,
                check=True,
            )


if __name__ == "__main__":
    main()
