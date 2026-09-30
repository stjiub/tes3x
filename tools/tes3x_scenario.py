"""Run a patch's game test in xemu, check its logs, and optionally record the result.

    python tools/tes3x_scenario.py console profiles/one-mod.toml
    python tools/tes3x_scenario.py console profiles/one-mod.toml --record

A game test is tests/game/<patch>.toml. By default, --record writes a local result under
build/validation/patches/<patch>/ with tools/tes3x_validate.py; --results selects another local
result store.

    kind = "single"                                 # or "comparison"
    purpose = "the behavior this scenario exercises"
    procedure = "how to repeat it"
    limitations = "what this scenario does not establish" # optional
    watch = "regex for the log lines the record keeps" # optional
    script = '''
    @start new
    wait 30
    assert player->getpos x == 61.00                 # fails the run unless it prints 61.00
    ...
    exit
    '''
    save = "fixture.ess"                             # optional, from build/saves/; the script
                                                     # loads it with `@start load` and its U: path
    enable = ["diagnostics", "console"]              # every build, besides the patch; default shown
    apply = "profile=0x00137C50"                      # optional valued patch spec
    xemu = ["--skip-intro", "--no-reboot"]           # tes3x_xemu.py options; default shown
    timeout = 300

    [expect]
    test = ["regex", "!regex"]                       # every regex must match; ! must not
    control = ["regex"]                              # comparison scenarios only
    [sequence]
    test = ["first regex", "later regex"]           # matches in this order
    [[compare]]                                       # comparison scenarios only
    pattern = "metric ([0-9]+)"                      # one numeric capture per log line
    relation = ">"                                   # every test value > its control value

A single scenario runs only the test build. A comparison scenario runs control and test builds
side by side; the control omits the patch and the test build adds it. Every run uses
--direct-engine and executes the scenario script from E:\\tes3xexec.txt.
"""

import argparse
import os
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from tes3x_diag import assertion_failures  # noqa: E402
import tes3x_patches as registry  # noqa: E402
from tes3x_test import (GAME_TESTS, TestError, game_test_profile, load_game_test,  # noqa: E402
                        make_fixture, read_toml, write_profile, comparison_failures,
                        sequence_failures)

DEFAULT_ENABLE = ["diagnostics", "console"]
DEFAULT_XEMU = ["--skip-intro", "--no-reboot"]
GLOBAL_FAILURES = (r"crash\.", r"hang\.detected", r"fatal\.")


def load(patch):
    try:
        return load_game_test(GAME_TESTS / f"{patch}.toml")
    except TestError as exc:
        sys.exit(str(exc))


def build_flags(spec, patch, role):
    """Pipeline options for one side. Only preset patches are chosen by name; the others come
    from every build, packaging, the profile, or `apply`."""
    enable = [n for n in spec.get("enable", DEFAULT_ENABLE) if n != patch]
    applied = spec.get("apply")
    by_name = registry.BY_NAME[patch]["selection"] == "preset"
    if role == "test" and by_name:
        enable.append(patch)
    flags = ["--preset", "minimal"]
    for name in enable:
        flags += ["--enable", name]
    if role == "control" and by_name:
        flags += ["--disable", patch]
    if role == "test" and applied:
        name, separator, value = applied.partition("=")
        if not separator or name != patch or not value:
            sys.exit(f"scenario apply must be {patch}=VALUE")
        option = {"title": "--title", "save-staging": "--save-staging",
                  "profile": "--profile-target"}.get(patch)
        if not option:
            sys.exit(f"scenario apply is unsupported for {patch}")
        flags += [option, value]
    return flags + spec.get("pipeline", [])


def test_profile(spec, profile, prefix, runs, config):
    """The profile path a run builds: PROFILE itself, or a generated one when the game test
    overlays profile settings or needs its fixture mod."""
    template = read_toml(profile)
    required = {name.casefold() for name in spec.get("required_mods", [])}
    enabled = {str(mod.get("id", mod.get("name", ""))).casefold()
               for mod in template.get("mods", []) if mod.get("enabled", True)}
    missing = required - enabled
    if missing:
        sys.exit("profile needs enabled mod(s): " + ", ".join(sorted(missing)))
    if not spec.get("profile") and not spec.get("fixture"):
        return profile
    library = None
    if spec.get("fixture"):
        paths = read_toml(config).get("paths", {})
        if "vanilla_root" not in paths:
            sys.exit(f"fixture game tests need paths.vanilla_root in {config}")
        vanilla = Path(paths["vanilla_root"]).expanduser()
        if not vanilla.is_absolute():
            vanilla = config.parent / vanilla
        esm = vanilla / "Data Files" / "Morrowind.esm"
        library = runs / f"{prefix}.library"
        make_fixture(spec["fixture"], esm, library)
    generated = runs / f"{prefix}.profile.toml"
    write_profile(generated, game_test_profile(template, spec, library))
    return str(generated)


def check(log, expectations, script, allow=(), sequence=()):
    """(ok, report lines) for a log against a list of regexes, `!` negating one, and the
    script's `assert` lines."""
    lines = log.splitlines()
    ok, report = True, []
    for item in expectations:
        negate = item.startswith("!")
        rx = re.compile(item[1:] if negate else item)
        hit = next((line for line in lines if rx.search(line)), None)
        good = (hit is None) if negate else (hit is not None)
        ok &= good
        report.append("  %s %s%s" % ("ok  " if good else "FAIL", item,
                                     "   <- " + hit.strip() if hit else ""))
    for item in (item for item in GLOBAL_FAILURES if item not in allow):
        rx = re.compile(item)
        hit = next((line for line in lines if rx.search(line)), None)
        good = hit is None
        ok &= good
        report.append("  %s !%s%s" % ("ok  " if good else "FAIL", item,
                                      "   <- " + hit.strip() if hit else ""))
    failures = assertion_failures(log, script)
    failures += sequence_failures(log, sequence)
    ok &= not failures
    report += ["  FAIL " + failure for failure in failures]
    asserts = sum(1 for line in script.splitlines() if line.strip().startswith("assert "))
    if asserts and not failures:
        report.append(f"  ok   {asserts} assertions")
    return ok, report


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("patch")
    ap.add_argument("profile", help="build profile the scenario run or runs use")
    ap.add_argument("--name", help="run name prefix under build/xemu/ (default: scenario-PATCH-N)")
    ap.add_argument("--only", choices=("control", "test"), help="run one side")
    ap.add_argument("--reuse", action="store_true",
                    help="check existing run folders of --name instead of booting them again")
    ap.add_argument("--record", action="store_true",
                    help="write a validation result when the complete scenario passes")
    ap.add_argument("--runner", help="xemu runner (default: tools/tes3x_xemu.py)")
    ap.add_argument("--config", help="local config (default: tes3x.local.toml in the working directory)")
    ap.add_argument("--save-root", help="private save fixtures (default: build/saves)")
    ap.add_argument("--results", default="build/validation",
                    help="local validation store (default: build/validation)")
    a = ap.parse_args()

    spec = load(a.patch)
    config = Path(a.config or Path.cwd() / "tes3x.local.toml").resolve()
    workspace = config.parent
    runner = Path(a.runner).resolve() if a.runner else ROOT / "tools" / "tes3x_xemu.py"
    if not runner.is_file():
        sys.exit(f"xemu runner not found: {runner}")
    runs = workspace / "build" / "xemu"
    results_root = Path(a.results)
    if not results_root.is_absolute():
        results_root = workspace / results_root
    save_root = Path(a.save_root) if a.save_root else workspace / "build" / "saves"
    if not save_root.is_absolute():
        save_root = workspace / save_root
    profile_arg = str(Path(a.profile).resolve())
    env = dict(os.environ)
    env["TES3X_CONFIG"] = str(config)
    prefix = a.name
    if a.reuse and not prefix:
        sys.exit("--reuse needs --name")
    if not prefix:
        n = 1
        while any((runs / f"scenario-{a.patch}-{n}-{r}").exists() for r in ("control", "test")):
            n += 1
        prefix = f"scenario-{a.patch}-{n}"
    script = runs / f"{prefix}.exec.txt"
    runs.mkdir(parents=True, exist_ok=True)
    if not a.reuse:
        script.write_text(spec["script"].strip() + "\n", encoding="ascii", newline="\n")

    wanted = ("test",) if spec["kind"] == "single" else ("control", "test")
    roles = [role for role in wanted if not a.only or role == a.only]
    if a.only and a.only not in wanted:
        sys.exit(f"--only {a.only} does not apply to a {spec['kind']} scenario")
    saves = []
    if spec.get("save"):
        save = save_root / spec["save"]
        if not save.is_file():
            sys.exit(f"scenario save not found: {save}")
        saves = ["--save", str(save)]

    profile = profile_arg if a.reuse else test_profile(spec, profile_arg, prefix, runs, config)

    # Both sides build and boot at once; each has its own folder, disk overlay and xemu config.
    procs = {}
    for role in roles:
        folder = runs / f"{prefix}-{role}"
        if a.reuse:
            if not folder.is_dir():
                sys.exit(f"--reuse: no run folder {folder}")
            continue
        cmd = [sys.executable, str(runner), folder.name, profile,
               "--direct-engine", "--exec", str(script), *saves,
               "--timeout", str(spec.get("timeout", 300)),
               *spec.get("xemu", DEFAULT_XEMU), "--", *build_flags(spec, a.patch, role)]
        print(f"== {role}: {' '.join(cmd[1:])}", flush=True)
        out = open(runs / f"{prefix}-{role}.out", "w")
        procs[role] = (subprocess.Popen(cmd, stdout=out, stderr=subprocess.STDOUT,
                                       cwd=workspace, env=env), out)
    for role, (proc, out) in procs.items():
        proc.wait()
        out.close()
        if proc.returncode:
            sys.exit(f"{role} run failed; see {out.name}")

    results, logs = {}, {}
    for role in roles:
        folder = runs / f"{prefix}-{role}"
        log_path = folder / "tes3xlog.txt"
        log = log_path.read_text(encoding="latin-1") if log_path.is_file() else ""
        logs[role] = log
        ok, report = check(log, spec["expect"].get(role, []), spec["script"],
                           spec.get("allow", []), spec.get("sequence", {}).get(role, []))
        if not log:
            ok, report = False, ["  FAIL no log recovered"]
        results[role] = (folder, ok)
        print(f"{role}: {'pass' if ok else 'FAIL'}", *report, sep="\n")

    if set(logs) == {"control", "test"}:
        failures = comparison_failures(logs, spec.get("compare", []))
        if failures:
            results["test"] = (results["test"][0], False)
            print("comparison: FAIL", *["  FAIL " + failure for failure in failures], sep="\n")
        elif spec.get("compare"):
            print(f"comparison: pass ({len(spec['compare'])} numeric checks)")

    if not all(ok for _, ok in results.values()):
        sys.exit("scenario failed")
    if a.record:
        if set(results) != set(wanted):
            sys.exit("--record needs the complete scenario")
        cmd = [sys.executable, str(ROOT / "tools" / "tes3x_validate.py"),
               "--results", str(results_root), "record",
               a.patch, "--env", "xemu",
               "--scenario", str(GAME_TESTS / f"{a.patch}.toml"),
               "--test", str(results["test"][0])]
        if spec["kind"] == "comparison":
            cmd += ["--control", str(results["control"][0])]
        subprocess.run(cmd, check=True, cwd=workspace, env=env)


if __name__ == "__main__":
    main()
