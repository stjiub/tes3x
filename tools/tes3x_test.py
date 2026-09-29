#!/usr/bin/env python3
"""Build and smoke-test an exact TES3X profile in xemu.

Runs go through tools/tes3x_xemu.py, which reads [xemu] from the local config.
"""

import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tomllib

from tes3x_diag import assertion_failures
from tes3x_pipeline import validate_profile
from tes3x_library import CATALOG_NAME, dependency_order, discover_library, load_library


ROOT = Path(__file__).resolve().parents[1]
GLOBAL_FAILURES = (r"crash\.", r"hang\.detected", r"fatal\.")


class TestError(ValueError):
    pass


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_toml(path):
    try:
        with open(path, "rb") as stream:
            return tomllib.load(stream)
    except FileNotFoundError as exc:
        raise TestError(f"missing {path}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise TestError(f"{path}: {exc}") from exc


def load_scenario(path):
    scenario = read_toml(path)
    allowed = {"kind", "purpose", "procedure", "limitations", "watch", "timeout", "xemu",
               "script", "expect"}
    unknown = set(scenario) - allowed
    if unknown:
        raise TestError(f"{path}: unknown fields: {', '.join(sorted(unknown))}")
    if scenario.get("kind") != "single":
        raise TestError(f"{path}: profile tests must be single scenarios")
    for field in ("purpose", "procedure", "script"):
        if not isinstance(scenario.get(field), str) or not scenario[field].strip():
            raise TestError(f"{path}: {field} must be a non-empty string")
    if not isinstance(scenario.get("watch", ""), str):
        raise TestError(f"{path}: watch must be a regular expression")
    if type(scenario.get("timeout", 300)) not in (int, float) or scenario.get("timeout", 300) <= 0:
        raise TestError(f"{path}: timeout must be greater than zero")
    if not isinstance(scenario.get("xemu", []), list) or any(
            not isinstance(value, str) for value in scenario.get("xemu", [])):
        raise TestError(f"{path}: xemu must be an array of strings")
    expect = scenario.get("expect", {})
    if not isinstance(expect, dict) or not isinstance(expect.get("test"), list) or not expect["test"]:
        raise TestError(f"{path}: expect.test must be a non-empty array")
    for expression in [*expect["test"], *GLOBAL_FAILURES, scenario.get("watch", ".")]:
        if not isinstance(expression, str):
            raise TestError(f"{path}: expectations must be strings")
        try:
            re.compile(expression.removeprefix("!"))
        except re.error as exc:
            raise TestError(f"{path}: invalid regular expression {expression!r}: {exc}") from exc
    return scenario


def check_log(path, scenario):
    if not path.is_file():
        return False, ["no log recovered"], []
    lines = path.read_text(encoding="latin-1").splitlines()
    failures = []
    for item in scenario["expect"]["test"]:
        negate = item.startswith("!")
        expression = item[1:] if negate else item
        found = any(re.search(expression, line) for line in lines)
        if found == negate:
            failures.append(item)
    for expression in GLOBAL_FAILURES:
        if any(re.search(expression, line) for line in lines):
            failures.append("!" + expression)
    failures += assertion_failures("\n".join(lines), scenario["script"])
    watch = re.compile(scenario.get("watch", r"exec[.>]|assert[.>]|diag\.|crash\.|hang\.|fatal\."))
    observed = [line.rstrip("\r") for line in lines if watch.search(line)]
    return not failures, failures, observed


def find_runner(value):
    if value:
        path = Path(value).resolve()
        if not path.is_file():
            raise TestError(f"xemu runner not found: {path}")
        return path
    candidates = [ROOT / "tools" / "tes3x_xemu.py"]
    for path in candidates:
        if path.is_file() and path.resolve() != Path(__file__).resolve():
            return path.resolve()
    raise TestError("xemu runner not found; pass --runner PATH")


def safe_name(value):
    return re.sub(r"[^a-z0-9._-]+", "-", value.lower()).strip("-._") or "profile"


def load_json(path):
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None


def toml_value(value):
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if type(value) is bool:
        return str(value).lower()
    if type(value) in (int, float):
        return str(value)
    if isinstance(value, list):
        return "[" + ", ".join(toml_value(item) for item in value) + "]"
    raise TestError(f"cannot write profile value {value!r}")


def write_profile(path, profile):
    """Write the supported profile schema for an ephemeral library test."""
    lines = []
    for section in ("profile", "rules", "patches", "preferences", "package", "ini"):
        values = profile.get(section)
        if not values:
            continue
        lines += [f"[{section}]"]
        for key, value in values.items():
            name = key if re.fullmatch(r"[A-Za-z0-9_-]+", key) else json.dumps(key)
            lines.append(f"{name} = {toml_value(value)}")
        lines.append("")
    for mod in profile.get("mods", []):
        lines.append("[[mods]]")
        for key, value in mod.items():
            lines.append(f"{key} = {toml_value(value)}")
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8", newline="\n")


def run_profile(args):
    profile_path = Path(args.profile).resolve()
    profile = read_toml(profile_path)
    validate_profile(profile)
    scenario_path = Path(args.scenario).resolve()
    scenario = load_scenario(scenario_path)
    runner = find_runner(args.runner)
    # The runner writes build/xemu/ under its working folder and reads the config found there.
    workspace = Path(args.config).resolve().parent if getattr(args, "config", None) else Path.cwd()
    run_root = workspace / "build" / "xemu"
    work_root = Path(args.work_root).resolve()
    work_root.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    scenario_name = safe_name(scenario_path.stem)
    profile_name = profile["profile"]["name"]
    run_name = safe_name(f"profile-{profile_name}-{scenario_name}-{timestamp}")
    run_dir = run_root / run_name
    script = work_root / f"{run_name}.exec.txt"
    script.write_text(scenario["script"].strip() + "\n", encoding="ascii", newline="\n")

    command = [sys.executable, str(runner), run_name, str(profile_path), "--direct-engine",
               "--exec", str(script), "--timeout", str(scenario.get("timeout", 300)),
               *scenario.get("xemu", [])]
    if args.keep_artifacts == "always":
        command.append("--keep-build")
    # These are test instrumentation, not a second build profile. The recorded pipeline marker
    # makes their presence explicit, while mod order, package choices and ordinary patches remain
    # exactly those of the selected profile.
    pipeline_args = []
    if getattr(args, "config", None):
        pipeline_args += ["--config", str(Path(args.config).resolve())]
    command += ["--", *pipeline_args, "--enable", "diagnostics", "--enable", "console",
                *args.pipeline_arg]
    print("==", " ".join(command[1:]), flush=True)
    env = dict(os.environ)
    if getattr(args, "config", None):
        env["TES3X_CONFIG"] = str(Path(args.config).resolve())
    process = subprocess.run(command, cwd=workspace, env=env)
    log = run_dir / "tes3xlog.txt"
    passed, failures, observed = check_log(log, scenario)
    if process.returncode:
        passed = False
        failures.insert(0, f"runner exited {process.returncode}")
    result = {
        "schema": 1,
        "date": datetime.date.today().isoformat(),
        "result": "pass" if passed else "fail",
        "profile": {"name": profile_name, "sha256": sha256_file(profile_path)},
        "scenario": {"name": scenario_name, "sha256": sha256_file(scenario_path),
                     "purpose": scenario["purpose"], "procedure": scenario["procedure"],
                     "limitations": scenario.get("limitations", "")},
        "expect": scenario["expect"]["test"],
        "script_sha256": sha256_file(script),
        "failures": failures,
        "observed": observed,
        "run": load_json(run_dir / ".tes3x-run.json"),
        "log_sha256": sha256_file(log) if log.is_file() else None,
    }
    result_path = work_root / f"{run_name}.json"
    result_path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    if args.record:
        record_root = Path(args.results).resolve() / safe_name(profile_name)
        record_root.mkdir(parents=True, exist_ok=True)
        record = record_root / f"{timestamp}-{scenario_name}-xemu.json"
        durable = dict(result)
        if log.is_file():
            log_name = record.with_suffix(".log").name
            shutil.copy2(log, record_root / log_name)
            durable["log"] = log_name
        record.write_text(json.dumps(durable, indent=2) + "\n", encoding="utf-8")
        print(f"recorded {record}")
    keep = args.keep_artifacts == "always" or (args.keep_artifacts == "failure" and not passed)
    if not keep and run_dir.is_dir():
        # The durable result and cited log have already left the run directory.
        shutil.rmtree(run_dir)
        print(f"removed successful run artifacts: {run_dir}")
    print(f"{profile_name}/{scenario_name}: {'pass' if passed else 'FAIL'}"
          + ("" if passed else ": " + ", ".join(failures)))
    return 0 if passed else 1


def run_library(args):
    template_path = Path(args.profile).resolve()
    template = read_toml(template_path)
    validate_profile(template)
    library_value = args.library or template["profile"].get("library", "")
    if not library_value:
        raise TestError("--library-all needs --library or profile.library")
    library_root = Path(library_value).resolve()
    catalog = (load_library(library_root) if (library_root / CATALOG_NAME).is_file()
               else discover_library(library_root))
    wanted = set(args.mod)
    unknown = wanted - set(catalog)
    if unknown:
        raise TestError("unknown library mod ids: " + ", ".join(sorted(unknown)))
    outcomes = []
    generated = Path(args.work_root).resolve() / "library-profiles"
    generated.mkdir(parents=True, exist_ok=True)
    for mod_id, mod in catalog.items():
        if wanted and mod_id not in wanted:
            continue
        versions = mod["releases"].values()
        if not args.all_versions:
            version = mod["default"] or next(iter(mod["releases"]))
            versions = [mod["releases"][version]]
        for release in versions:
            generated_profile = json.loads(json.dumps(template))
            generated_profile["profile"]["name"] = safe_name(
                f"library-{mod_id}-{release['version']}")
            generated_profile["profile"]["library"] = library_root.as_posix()
            generated_profile["mods"] = []
            for index, selected_id in enumerate(
                    dependency_order(mod_id, catalog, release["version"]), 1):
                selected_mod = catalog[selected_id]
                selected_version = (release["version"] if selected_id == mod_id else
                                    selected_mod["default"] or next(iter(selected_mod["releases"])))
                selected_release = selected_mod["releases"][selected_version]
                generated_profile["mods"].append({
                    "id": selected_id,
                    "version": selected_version,
                    "components": [item["id"] for item in selected_release["components"].values()
                                   if item["default"]],
                    "order": index * 10,
                })
            path = generated / f"{generated_profile['profile']['name']}.toml"
            write_profile(path, generated_profile)
            child = argparse.Namespace(**vars(args))
            child.profile = str(path)
            print(f"\n## {mod['name']} {release['version']}", flush=True)
            outcomes.append(run_profile(child))
    if not outcomes:
        raise TestError("no installed library releases selected")
    passed = sum(result == 0 for result in outcomes)
    print(f"\nlibrary smoke tests: {passed} pass, {len(outcomes) - passed} fail")
    return 0 if passed == len(outcomes) else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("profile", help="the exact profile to build and test")
    parser.add_argument("--scenario", default=ROOT / "tests" / "smoke.toml",
                        help="single-run scenario TOML (default: tests/smoke.toml)")
    parser.add_argument("--runner", help="xemu runner to use instead of tools/tes3x_xemu.py")
    parser.add_argument("--config", help="local TES3X config passed to the build pipeline")
    parser.add_argument("--work-root", default="build/profile-tests",
                        help="small transient scripts/results (default: build/profile-tests)")
    parser.add_argument("--results", default="profile-tests/results",
                        help="durable --record destination (default: profile-tests/results)")
    parser.add_argument("--record", action="store_true", help="retain compact result and log")
    parser.add_argument("--library-all", action="store_true",
                        help="use PROFILE as a template and test each managed library mod alone")
    parser.add_argument("--library", help="library root override for --library-all")
    parser.add_argument("--all-versions", action="store_true",
                        help="with --library-all, test every installed version")
    parser.add_argument("--mod", action="append", default=[],
                        help="with --library-all, test only this mod id (repeatable)")
    parser.add_argument("--keep-artifacts", choices=("always", "failure", "never"),
                        default="failure", help="retain ISO/pipeline output (default: failure)")
    parser.add_argument("--pipeline-arg", action="append", default=[],
                        help="argument passed to tes3x_pipeline.py (repeatable)")
    args = parser.parse_args(argv)
    try:
        return run_library(args) if args.library_all else run_profile(args)
    except (TestError, ValueError, OSError, subprocess.SubprocessError) as exc:
        raise SystemExit(str(exc)) from exc


if __name__ == "__main__":
    raise SystemExit(main())
