"""Find out whether a newer TES3X release is on GitHub."""

import json
import re
import urllib.error
import urllib.request

from tes3x import __version__
from tes3x.paths import bundled

LATEST_URL = "https://api.github.com/repos/stjiub/tes3x/releases/latest"
NUMBERS = re.compile(r"v?(\d+(?:\.\d+)*)")


def current():
    """The running version text: the portable folder's VERSION, else the package's."""
    path = bundled("VERSION")
    return path.read_text(encoding="utf-8").strip() if path.is_file() else __version__


def numbers(text):
    match = NUMBERS.match(text.strip())
    return tuple(int(part) for part in match[1].split(".")) if match else ()


def latest(url=LATEST_URL):
    """(tag, page) of the newest release, or None when there is none yet."""
    request = urllib.request.Request(url, headers={"User-Agent": "TES3X",
                                                   "Accept": "application/vnd.github+json"})
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            release = json.load(response)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise
    return release["tag_name"], release["html_url"]


def newer(tag, running=None):
    """Whether TAG is a later release than the running version. A development build counts as
    ahead of the release it follows."""
    return numbers(tag) > numbers(running or current())
