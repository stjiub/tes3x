"""Progress lines for a front end: `@progress STAGE DONE TOTAL UNIT [NAME]`.

A front end that wants them sets TES3X_PROGRESS in the tool's environment; without it the tools
print their usual text and these helpers do nothing.
"""

import os
import time

ENV = "TES3X_PROGRESS"
PREFIX = "@progress "
INTERVAL = 0.25  # seconds between lines from a counted loop


def enabled():
    return bool(os.environ.get(ENV))


def emit(stage, done, total, unit, name=""):
    print(f"{PREFIX}{stage} {done} {total} {unit} {name}".rstrip(), flush=True)


def parse(line):
    """(stage, done, total, unit, name) from an `@progress` line, or None."""
    if not line.startswith(PREFIX):
        return None
    parts = line[len(PREFIX):].strip().split(None, 4)
    if len(parts) < 4:
        return None
    try:
        done, total = int(parts[1]), int(parts[2])
    except ValueError:
        return None
    return parts[0], done, total, parts[3], parts[4] if len(parts) > 4 else ""


class Steps:
    """Named phases of one run: `begin("pack")` reports the phase about to start."""

    def __init__(self, stage, total):
        self.stage, self.total, self.done = stage, total, 0

    def begin(self, name):
        if enabled():
            emit(self.stage, self.done, self.total, "steps", name)
        self.done += 1


class Counter:
    """Counted work inside a phase, reported at most every INTERVAL seconds."""

    def __init__(self, stage, total, unit="files"):
        self.stage, self.total, self.unit = stage, total, unit
        self.done = 0
        self.last = 0.0
        self.on = enabled()

    def step(self, name=""):
        self.done += 1
        if not self.on:
            return
        now = time.monotonic()
        if self.done == self.total or now - self.last >= INTERVAL:
            self.last = now
            emit(self.stage, self.done, self.total, self.unit, name)
