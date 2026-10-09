"""Build hook: a wheel carries the repository data its commands read, as tes3x/_data."""
import shutil
from pathlib import Path

from setuptools import setup
from setuptools.command.build_py import build_py

ROOT = Path(__file__).resolve().parent
# Everything tes3x.paths.resource() names but the portable folder's own files (VERSION, the
# manager's XBEs, externals/); test_pipeline checks the two agree.
DATA = ("patches.toml", "candidates.toml", "catalog.toml", "hooks", "symbols", "examples",
        "assets/menu", "addons", "tests/game", "keys/release.pub", "manager/mgr.h")
SKIP = shutil.ignore_patterns("__pycache__", "*.pyc")


class BuildWithData(build_py):
    def run(self):
        super().run()
        if self.editable_mode:  # an editable install reads the checkout itself
            return
        target = Path(self.build_lib) / "tes3x" / "_data"
        for name in DATA:
            source = ROOT / name
            if source.is_dir():
                shutil.copytree(source, target / name, ignore=SKIP, dirs_exist_ok=True)
            else:
                (target / name).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target / name)


setup(cmdclass={"build_py": BuildWithData})
