"""Compatibility only: run `python -m tes3x.patches`, or import tes3x.patches."""
import os
import sys
from pathlib import Path

SRC = str(Path(__file__).resolve().parents[1] / "src")
sys.path.insert(0, SRC)
# Tools this one starts as `python -m tes3x ...` import the same package.
os.environ["PYTHONPATH"] = os.pathsep.join(filter(None, (SRC, os.environ.get("PYTHONPATH"))))
if __name__ == "__main__":
    import runpy
    runpy.run_module("tes3x.patches", run_name="__main__")
else:
    import tes3x.patches
    sys.modules[__name__] = tes3x.patches
