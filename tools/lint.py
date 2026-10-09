"""Check project Python files, excluding bundled third-party code."""
from pathlib import Path
from pyflakes.api import checkPath
from pyflakes.reporter import Reporter
import sys


def main():
    root = Path(__file__).resolve().parents[1]
    files = [root / "setup.py"]
    for folder in ("src", "tests", "tools"):
        files.extend(path for path in (root / folder).rglob("*.py")
                     if "_vendor" not in path.parts and "__pycache__" not in path.parts)
    reporter = Reporter(sys.stdout, sys.stderr)
    return int(bool(sum(checkPath(str(path), reporter) for path in sorted(files))))


if __name__ == "__main__":
    sys.exit(main())
