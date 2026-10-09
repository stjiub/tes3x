"""Compatibility only: run `python -m tes3x.catalog`, or import tes3x.catalog."""
import os
import sys
from pathlib import Path

SRC = str(Path(__file__).resolve().parents[1] / "src")
sys.path.insert(0, SRC)
# Tools this one starts as `python -m tes3x ...` import the same package.
os.environ["PYTHONPATH"] = os.pathsep.join(filter(None, (SRC, os.environ.get("PYTHONPATH"))))
if __name__ == "__main__":
    import runpy
    runpy.run_module("tes3x.catalog", run_name="__main__")
else:
    import tes3x.catalog
    sys.modules[__name__] = tes3x.catalog
