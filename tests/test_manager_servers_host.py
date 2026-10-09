"""Run the manager's real servers.ini rewrite with bounded host allocations."""

import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

from tes3x.payload import PayloadError, find_tool

ROOT = Path(__file__).resolve().parents[1]


def build(folder, clang, source=None):
    folder = Path(folder)
    header = (ROOT / 'manager' / 'mgr.h').read_text(encoding='utf-8')
    layout = re.search(r'struct server \{.*?\n\};', header, re.S).group()
    path_size = re.search(r'^#define PATH_MAX_MGR \d+', header, re.M).group()
    (folder / 'mgr.h').write_text(
        '#pragma once\n#include <stddef.h>\n' + path_size + '\n' + layout + '\n'
        'int read_file(const char *, unsigned char **, size_t *);\n'
        'int write_flushed(const char *, const void *, size_t);\n'
        'int name_cmp(const char *, const char *);\n'
        'int name_cmp_n(const char *, const char *, size_t);\n', encoding='utf-8')
    source = source or (ROOT / 'manager' / 'servers.c').read_text(encoding='utf-8')
    source = source.split('/* --- the session --- */')[0]
    source = re.sub(r'^#include <(?:lwip/[^>]+|windows.h|xboxkrnl/[^>]+)>\n', '',
                    source, flags=re.M)
    source = source.replace('"../hooks/monocypher.h"', '"monocypher.h"')
    source = source.replace('#include "../hooks/tes3xnoise.h"', '')
    (folder / 'servers-under-test.c').write_text(source, encoding='utf-8')
    for name in ('monocypher.h', 'monocypher.c'):
        shutil.copyfile(ROOT / 'hooks' / name, folder / name)
    exe = folder / ('servershost.exe' if os.name == 'nt' else 'servershost')
    subprocess.run([clang, '-std=c11', '-O0', '-g', f'-I{folder}',
                    str(ROOT / 'tests' / 'managerhost' / 'servers.c'),
                    str(folder / 'monocypher.c'), '-o', str(exe)],
                   capture_output=True, text=True, check=True)
    return exe


class ManagerServersHostTests(unittest.TestCase):
    def test_rewrite_with_maximum_fields(self):
        try:
            clang = find_tool('clang')
        except (SystemExit, PayloadError) as error:
            self.skipTest(f'needs clang: {error}')
        with tempfile.TemporaryDirectory() as folder:
            try:
                exe = build(folder, clang)
            except subprocess.CalledProcessError as error:
                self.fail(error.stderr)
            run = subprocess.run([str(exe)], capture_output=True, text=True, timeout=30)
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
