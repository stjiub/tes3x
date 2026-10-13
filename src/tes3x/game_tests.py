"""Run every game test against one or more retail engines in xemu and report what passed.

    tes3x game-tests profiles/retail.toml
    tes3x game-tests profiles/retail.toml --engine ntsc --engine pal --jobs 3
    tes3x game-tests profiles/retail.toml --engine all --only mcp-37 --only console

Each test in tests/game/ runs through `tes3x scenario`, so a comparison test boots two xemus;
--jobs counts tests, not emulators. Engines are named clean retail roots in the local config:

    [engines]
    ntsc = "D:/Morrowind/NTSC"
    pal = "D:/Morrowind/PAL"

Without --engine, the runs use [paths] vanilla_root. A test whose required_mods the profile
lacks is skipped unless --profile-for names a profile that has them. Run folders are
build/xemu/NAME-ENGINE-TEST*; the summary is build/xemu/NAME.summary.json.
"""

import argparse
from concurrent.futures import ThreadPoolExecutor
import datetime
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time

from tes3x.paths import CONFIG_HELP, data_dir, local_config
from tes3x.test import GAME_TESTS, TestError, load_game_test, read_toml


def engines(config, names):
    """[(name, root)] for the requested engine names; [("default", None)] for none."""
    if not names:
        return [("default", None)]
    table = read_toml(config).get("engines", {}) if config.is_file() else {}
    if "all" in names:
        names = list(table)
        if not names:
            sys.exit(f"--engine all: no [engines] table in {config}")
    chosen = []
    for name in dict.fromkeys(names):
        if name not in table:
            sys.exit(f"no engine {name!r} in [engines] of {config}"
                     + (f"; it has {', '.join(table)}" if table else ""))
        root = Path(table[name]).expanduser()
        root = root if root.is_absolute() else config.parent / root
        if not (root / "morrowind.xbe").is_file():
            sys.exit(f"engine {name}: no morrowind.xbe in {root}")
        chosen.append((name, root.resolve()))
    return chosen


def engine_hash(root, config):
    if root is None:
        paths = read_toml(config).get("paths", {}) if config.is_file() else {}
        if not paths.get("vanilla_root"):
            return "?"
        root = Path(paths["vanilla_root"]).expanduser()
        root = root if root.is_absolute() else config.parent / root
    xbe = root / "morrowind.xbe"
    return hashlib.sha256(xbe.read_bytes()).hexdigest() if xbe.is_file() else "?"


def missing_mods(spec, profile):
    required = {name.casefold() for name in spec.get("required_mods", [])}
    enabled = {str(mod.get("id", mod.get("name", ""))).casefold()
               for mod in read_toml(profile).get("mods", []) if mod.get("enabled", True)}
    return sorted(required - enabled)


def first_failure(output):
    """The side and first failed check of a scenario's output, else its last line."""
    side = ""
    for line in output.splitlines():
        text = line.strip()
        if text.endswith(": FAIL"):
            side = text.removesuffix(": FAIL") + ": "
        elif text.startswith("FAIL"):
            return side + text
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    return lines[-1] if lines else "no output"


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("profile", help="build profile every test uses")
    ap.add_argument("--engine", action="append", default=[],
                    help="engine name from [engines] in the local config, or all (repeatable)")
    ap.add_argument("--jobs", type=int, default=2, help="tests run at once (default 2)")
    ap.add_argument("--only", action="append", default=[], metavar="TEST",
                    help="run only this game test (repeatable)")
    ap.add_argument("--profile-for", action="append", default=[], metavar="TEST=PROFILE",
                    help="build this test from another profile, e.g. one with its required mods")
    ap.add_argument("--name", help="run name prefix (default: game-tests-DATE-TIME)")
    ap.add_argument("--config", help=CONFIG_HELP)
    ap.add_argument("--work-root", help="output root (default: TES3X data folder)")
    ap.add_argument("--save-root", help="private save fixtures (default: output root's build/saves)")
    a = ap.parse_args()
    if a.jobs < 1:
        sys.exit("--jobs must be at least 1")

    config = local_config(a.config).resolve()
    workspace = (Path(a.work_root) if a.work_root else data_dir()).resolve()
    runs = workspace / "build" / "xemu"
    runs.mkdir(parents=True, exist_ok=True)
    prefix = a.name or "game-tests-" + datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    profile = Path(a.profile).resolve()
    overrides = {}
    for item in a.profile_for:
        test, separator, path = item.partition("=")
        if not separator or not path:
            sys.exit("--profile-for must be TEST=PROFILE")
        overrides[test] = Path(path).resolve()

    tests = sorted(path.stem for path in GAME_TESTS.glob("*.toml"))
    unknown = sorted(set(a.only) - set(tests))
    if unknown:
        sys.exit("no game test: " + ", ".join(unknown))
    if a.only:
        tests = [test for test in tests if test in a.only]
    specs = {}
    for test in tests:
        try:
            specs[test] = load_game_test(GAME_TESTS / f"{test}.toml")
        except TestError as exc:
            sys.exit(str(exc))

    chosen = engines(config, a.engine)
    for name, root in chosen:
        print(f"engine {name}: {root or '[paths] vanilla_root'}  "
              f"sha256 {engine_hash(root, config)[:16]}", flush=True)
    hashes = {engine_hash(root, config) for _, root in chosen}
    if len(hashes) < len(chosen):
        print("warning: two engines have the same morrowind.xbe")

    jobs = [(name, root, test) for name, root in chosen for test in tests]
    results, lock = [], threading.Lock()

    def run(job):
        name, root, test = job
        test_profile = overrides.get(test, profile)
        result = {"engine": name, "test": test, "profile": str(test_profile)}
        missing = missing_mods(specs[test], test_profile)
        if missing:
            result.update(status="skip", reason="profile lacks " + ", ".join(missing))
        else:
            run_name = f"{prefix}-{name}-{test}"
            env = dict(os.environ, TES3X_CONFIG=str(config))
            if root:
                env["TES3X_VANILLA_ROOT"] = str(root)
            cmd = [sys.executable, "-m", "tes3x", "scenario", test, str(test_profile),
                   "--work-root", str(workspace), "--config", str(config), "--name", run_name]
            if a.save_root:
                cmd += ["--save-root", a.save_root]
            start = time.monotonic()
            done = subprocess.run(cmd, cwd=workspace, env=env, capture_output=True, text=True,
                                  errors="replace")
            output = done.stdout + done.stderr
            report = runs / f"{run_name}.scenario.txt"
            report.write_text(output, encoding="utf-8")
            result.update(status="pass" if done.returncode == 0 else "FAIL",
                          seconds=round(time.monotonic() - start), report=str(report))
            if done.returncode:
                result["reason"] = first_failure(output)
        with lock:
            results.append(result)
            print(f"[{len(results)}/{len(jobs)}] {name} {test}: {result['status']}"
                  + (f"  {result['reason']}" if result.get("reason") else ""), flush=True)

    with ThreadPoolExecutor(a.jobs) as pool:
        list(pool.map(run, jobs))

    names = [name for name, _ in chosen]
    status = {(r["engine"], r["test"]): r["status"] for r in results}
    width = max(map(len, tests)) + 2
    print("\n" + "test".ljust(width) + "".join(name.ljust(8) for name in names))
    for test in tests:
        print(test.ljust(width) + "".join(status[name, test].ljust(8) for name in names))
    failures = [r for r in results if r["status"] == "FAIL"]
    for r in sorted(failures, key=lambda r: (r["engine"], r["test"])):
        print(f"\nFAIL {r['engine']} {r['test']}: {r['reason']}\n  {r['report']}")
    for name in names:
        counts = [r["status"] for r in results if r["engine"] == name]
        print(f"{name}: {counts.count('pass')} passed, {counts.count('FAIL')} failed, "
              f"{counts.count('skip')} skipped")
    summary = runs / f"{prefix}.summary.json"
    summary.write_text(json.dumps({
        "profile": str(profile),
        "engines": [{"name": name, "root": str(root) if root else None,
                     "sha256": engine_hash(root, config)} for name, root in chosen],
        "results": sorted(results, key=lambda r: (r["engine"], r["test"])),
    }, indent=1) + "\n", encoding="utf-8")
    print(f"summary: {summary}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
