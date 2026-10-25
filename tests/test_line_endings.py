import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CRLF_OK = {".bat", ".cmd", ".ps1"}


@unittest.skipUnless(shutil.which("git") and (ROOT / ".git").exists(), "needs a git checkout")
class LineEndingTests(unittest.TestCase):
    def test_committed_text_files_use_lf(self):
        listing = subprocess.run(["git", "ls-files", "--eol"], cwd=ROOT, check=True,
                                 capture_output=True, text=True).stdout
        bad = []
        for line in listing.splitlines():
            info, _, name = line.partition("\t")
            if info.split()[0] in ("i/crlf", "i/mixed") and Path(name).suffix not in CRLF_OK:
                bad.append(name)
        self.assertEqual(bad, [], "committed with CRLF; the repository stores LF")


if __name__ == "__main__":
    unittest.main()
