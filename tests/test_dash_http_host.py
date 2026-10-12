"""Exercise the Dash's file receiver with dropped connections and saved prefixes."""

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from tes3x.payload import PayloadError, find_tool

ROOT = Path(__file__).resolve().parents[1]


class DashDownloadTests(unittest.TestCase):
    def build_and_run(self, harness, base=False):
        try:
            clang = find_tool('clang')
        except (SystemExit, PayloadError) as error:
            self.skipTest(f'needs clang: {error}')
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            source = (ROOT / 'manager/http.c').read_text(encoding='utf-8')
            header = source[source.index('static const char *header('):
                            source.index('/* A chunked body')]
            receiver = source[source.index('#define SAVE_BUFFER'):]
            (folder / 'receiver.c').write_text(header + receiver, encoding='utf-8')
            for name in ('sha256.c', 'sha256.h'):
                shutil.copyfile(ROOT / 'manager' / name, folder / name)
            sources = [str(folder / 'sha256.c')]
            if base:
                install = (ROOT / 'manager/install.c').read_text(encoding='utf-8')
                start = install.index('static const char *install_base(')
                end = install.index('const char *server_install(', start)
                (folder / 'base-under-test.c').write_text(install[start:end], encoding='utf-8')
                for name in ('json.h', 'json.c'):
                    shutil.copyfile(ROOT / 'manager' / name, folder / name)
                sources.append(str(folder / 'json.c'))
            exe = folder / ('http-test.exe' if os.name == 'nt' else 'http-test')
            compiled = subprocess.run([clang, '-std=c11', '-O0', f'-I{folder}',
                                       str(ROOT / 'tests/managerhost' / harness),
                                       *sources, '-o', str(exe)],
                                      capture_output=True, text=True)
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            run = subprocess.run([str(exe)], cwd=folder, capture_output=True, text=True,
                                 timeout=30)
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)

    def test_receiver_recovers_and_checks_content(self):
        self.build_and_run('http.c')

    def test_overlay_base_installs_before_selection_and_resumes(self):
        self.build_and_run('base.c', base=True)
