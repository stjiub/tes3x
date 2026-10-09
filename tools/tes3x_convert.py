"""Compatibility only: run `python -m tes3x.convert`, or import tes3x.convert."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
if __name__ == "__main__":
    import runpy
    runpy.run_module("tes3x.convert", run_name="__main__")
else:
    import tes3x.convert
    sys.modules[__name__] = tes3x.convert
