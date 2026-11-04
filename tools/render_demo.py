"""Record the isolated demo and render its owned documentation assets."""

import argparse
import json
import os
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "docs/assets"


def run(command: list[str]) -> None:
    subprocess.run(command, cwd=ROOT, check=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--review-dir", type=Path, help="Save a contact sheet outside the repository"
    )
    args = parser.parse_args()
    for command, expected in [
        (["asciinema", "--version"], "asciinema 3.2.1"),
        (["agg", "--version"], "agg 1.9.0"),
    ]:
        version = subprocess.check_output(command, text=True).strip()
        if version != expected:
            raise SystemExit(f"Expected {expected}; found {version}")
    ASSETS.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="relay-recording-") as folder:
        raw = Path(folder) / "raw.cast"
        environment = dict(os.environ, TERM="xterm-256color")
        subprocess.run(
            [
                "asciinema",
                "rec",
                "--output-format",
                "asciicast-v2",
                "--quiet",
                "--return",
                "--window-size",
                "86x27",
                "--capture-env",
                "TERM",
                "--title",
                "Relay standalone demo",
                "--command",
                "uv run --locked --quiet python tools/demo.py",
                str(raw),
            ],
            cwd=ROOT,
            env=environment,
            check=True,
        )
        lines = raw.read_text().splitlines()
        header = json.loads(lines[0])
        safe_header = {key: header[key] for key in ("version", "width", "height")}
        safe_header.update({"env": {"TERM": "xterm-256color"}, "title": "Relay standalone demo"})
        events = [json.loads(line) for line in lines[1:]]
        first_output = next(event[0] for event in events if event[1] == "o")
        for event in events:
            event[0] = max(0, round(event[0] - first_output, 6))
        recording = ASSETS / "relay-demo.cast"
        recording.write_text(
            json.dumps(safe_header) + "\n" + "\n".join(json.dumps(event) for event in events) + "\n"
        )
        # The controlled program prints no host context. Reject accidental diagnostics
        # or terminal metadata rather than trying to redact arbitrary recordings.
        output = recording.read_text()
        decoded = "".join(str(event[2]) for event in events if event[1] == "o")
        for marker in ("/home/", "/mnt/", "Traceback", "\u001b]", "user@", "SHELL"):
            if marker in output or marker in decoded:
                raise SystemExit("Unexpected private context or diagnostic in recording")
        run(
            [
                "agg",
                "--quiet",
                "--font-family",
                "DejaVu Sans Mono",
                "--font-size",
                "18",
                "--line-height",
                "1.25",
                "--theme",
                "github-dark",
                "--fps-cap",
                "12",
                "--last-frame-duration",
                "4",
                str(recording),
                str(ASSETS / "relay-demo.gif"),
            ]
        )
    run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-i",
            str(ASSETS / "relay-demo.gif"),
            "-vf",
            "reverse",
            "-frames:v",
            "1",
            "-update",
            "1",
            str(ASSETS / "relay-demo.png"),
        ]
    )
    if args.review_dir:
        args.review_dir.mkdir(parents=True, exist_ok=True)
        run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-y",
                "-i",
                str(ASSETS / "relay-demo.gif"),
                "-vf",
                "fps=1/4,scale=600:-1,tile=2x3",
                "-frames:v",
                "1",
                "-update",
                "1",
                str(args.review_dir / "contact-sheet.png"),
            ]
        )
    print("Rendered docs/assets/relay-demo.gif and static fallback")


if __name__ == "__main__":
    main()
