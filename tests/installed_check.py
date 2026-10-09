"""Run with an installed tes3x, from outside the checkout: it works with no repository at hand.

CI builds the wheel, installs it into a fresh venv and runs this with that venv's Python; and
runs it with a portable folder's Python, with TES3X_EXPECT_PORTABLE set.
"""
import os
import subprocess
import sys
from pathlib import Path

import tes3x
import tes3x.net as net
import tes3x.patches as registry
from tes3x.paths import PACKAGED, PORTABLE, bundled, resource
from tes3x.payload import source_text

assert PACKAGED.is_dir(), f"no _data in {Path(tes3x.__file__).parent}"
for parts in (('patches.toml',), ('candidates.toml',), ('catalog.toml',),
              ('hooks', 'xboxkrnl.exe.def'), ('symbols', 'curated.json'),
              ('symbols', 'structs.json'), ('examples', 'profile.toml'),
              ('examples', 'starts.toml'), ('assets', 'menu'), ('addons', 'console', 'console.py'),
              ('tests', 'game', 'smoke.toml'), ('manager', 'mgr.h'), ('keys', 'release.pub')):
    assert resource(*parts).exists(), parts
if os.environ.get('TES3X_EXPECT_PORTABLE'):
    assert PORTABLE, f"no TES3X.exe beside {Path(sys.prefix).parent}"
    for parts in (('VERSION',), ('externals', 'llvm', 'bin', 'clang.exe'),
                  ('externals', '7zip', '7z.exe')):
        assert bundled(*parts).is_file(), parts
assert registry.PATCHES, "no patches in the registry"
assert net.load_starts(net.STARTS), "no default start points"
assert '#include "multi/' not in source_text('tes3xmulti.c'), "multiplayer sections not found"
for command in (['tes3x.pipeline', '--help'], ['tes3x.net', '--help']):
    subprocess.run([sys.executable, '-m', *command], check=True, capture_output=True)
print(f"tes3x {tes3x.__version__} from {Path(tes3x.__file__).parent}: resources and commands ok")
