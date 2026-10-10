#!/usr/bin/env python3
"""Build and smoke-test an exact TES3X profile in xemu.

Runs go through tes3x xemu, which reads [xemu] from the local config.
"""

import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import sys
import tomllib

from tes3x.diag import assertion_failures
from tes3x.pipeline import validate_profile
from tes3x.library import CATALOG_NAME, dependency_order, discover_library, load_library
from tes3x.paths import data_dir, local_config, resource


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


GAME_TESTS = resource("tests", "game")
KINDS = {"single": ("test",), "comparison": ("control", "test")}
REQUIRED = ("kind", "purpose", "procedure", "script", "expect")
OPTIONAL = ("limitations", "watch", "timeout", "xemu", "save", "enable", "apply", "pipeline",
            "allow", "profile", "fixture", "sequence", "compare", "required_mods", "agent")
PROFILE_OVERLAY = ("profile", "rules", "preferences", "package", "ini")
FIXTURE_KEYS = {"script": str, "opcodes": list, "texture": bool, "dialogue": str}
AGENT_KEYS = {"after": str, "console": list, "fetch": list, "exit": bool}
FIXTURE_MOD = "tes3x-test"


def game_test_problems(test, where):
    """Everything wrong with a game test's fields, as messages."""
    problems = [f"{where}: missing {key}" for key in REQUIRED if key not in test]
    unknown = set(test) - set(REQUIRED) - set(OPTIONAL)
    if unknown:
        problems.append(f"{where}: unknown fields: {', '.join(sorted(unknown))}")
    for key in ("purpose", "procedure", "script"):
        if key in test and (not isinstance(test[key], str) or not test[key].strip()):
            problems.append(f"{where}: {key} must be a non-empty string")
    for key in ("limitations", "watch", "save", "apply"):
        if key in test and not isinstance(test[key], str):
            problems.append(f"{where}: {key} must be a string")
    timeout = test.get("timeout", 300)
    if type(timeout) not in (int, float) or timeout <= 0:
        problems.append(f"{where}: timeout must be greater than zero")
    for key in ("xemu", "enable", "pipeline", "allow", "required_mods"):
        if not isinstance(test.get(key, []), list) or any(
                not isinstance(value, str) for value in test.get(key, [])):
            problems.append(f"{where}: {key} must be an array of strings")
    for expression in test.get("allow", []):
        if isinstance(expression, str) and expression not in GLOBAL_FAILURES:
            problems.append(f"{where}: allow may only name {', '.join(GLOBAL_FAILURES)}")
    overlay = test.get("profile", {})
    if not isinstance(overlay, dict) or set(overlay) - set(PROFILE_OVERLAY) or any(
            not isinstance(value, dict) for value in overlay.values()):
        problems.append(f"{where}: profile may only hold tables {', '.join(PROFILE_OVERLAY)}")
    fixture = test.get("fixture", {})
    if not isinstance(fixture, dict) or set(fixture) - set(FIXTURE_KEYS) or any(
            type(fixture[key]) is not kind for key, kind in FIXTURE_KEYS.items() if key in fixture):
        problems.append(f"{where}: fixture takes script (string), opcodes (array), texture "
                        "(boolean) and dialogue (string)")
    elif any(type(opcode) is not int for opcode in fixture.get("opcodes", [])):
        problems.append(f"{where}: fixture.opcodes must be integers")
    agent = test.get("agent", {})
    if not isinstance(agent, dict) or set(agent) - set(AGENT_KEYS) or any(
            type(agent[key]) is not kind for key, kind in AGENT_KEYS.items() if key in agent) or any(
            not isinstance(value, str) for key in ("console", "fetch")
            for value in agent.get(key, [])):
        problems.append(f"{where}: agent takes after (string), console and fetch (arrays of "
                        "strings) and exit (boolean)")
    if test.get("kind") not in KINDS:
        return problems + [f"{where}: kind must be single or comparison"]
    roles = KINDS[test["kind"]]
    expect = test.get("expect")
    if not isinstance(expect, dict) or set(expect) != set(roles):
        return problems + [f"{where}: a {test['kind']} test needs expect.{' and expect.'.join(roles)}"]
    expressions = [test.get("watch", ".")]
    for role in roles:
        if not isinstance(expect[role], list) or not expect[role] or any(
                not isinstance(item, str) for item in expect[role]):
            problems.append(f"{where}: expect.{role} must be a non-empty array of strings")
        else:
            expressions += expect[role]
    for expression in expressions:
        try:
            re.compile(expression.removeprefix("!") if isinstance(expression, str) else "")
        except re.error as exc:
            problems.append(f"{where}: invalid regular expression {expression!r}: {exc}")
    sequence = test.get("sequence", {})
    if not isinstance(sequence, dict) or set(sequence) - set(roles):
        problems.append(f"{where}: sequence may only contain {', '.join(roles)}")
    else:
        for role, items in sequence.items():
            if not isinstance(items, list) or not items or any(
                    not isinstance(item, str) for item in items):
                problems.append(f"{where}: sequence.{role} must be a non-empty array of strings")
                continue
            for expression in items:
                try:
                    re.compile(expression)
                except re.error as exc:
                    problems.append(f"{where}: invalid sequence expression {expression!r}: {exc}")
    comparisons = test.get("compare", [])
    if comparisons and test["kind"] != "comparison":
        problems.append(f"{where}: compare needs a comparison test")
    if not isinstance(comparisons, list):
        problems.append(f"{where}: compare must be an array of tables")
    else:
        for comparison in comparisons:
            if not isinstance(comparison, dict) or set(comparison) != {"pattern", "relation"}:
                problems.append(f"{where}: each compare needs pattern and relation")
                continue
            if comparison["relation"] not in (">", ">=", "<", "<=", "==", "!="):
                problems.append(f"{where}: unknown compare relation {comparison['relation']!r}")
            try:
                pattern = re.compile(comparison["pattern"])
                if pattern.groups != 1:
                    problems.append(f"{where}: compare pattern needs one capture group")
            except (TypeError, re.error) as exc:
                problems.append(f"{where}: invalid compare pattern: {exc}")
    return problems


def load_game_test(path):
    test = read_toml(path)
    problems = game_test_problems(test, path)
    if problems:
        raise TestError("; ".join(str(problem) for problem in problems))
    return test


def sequence_failures(log, patterns):
    """Ordered regular expressions not found on successive log lines."""
    lines = log.splitlines()
    position, failures = 0, []
    for pattern in patterns:
        found = next((index for index in range(position, len(lines))
                      if re.search(pattern, lines[index])), None)
        if found is None:
            failures.append(f"sequence:{pattern}")
            break
        position = found + 1
    return failures


def comparison_failures(logs, comparisons):
    """Numeric test-versus-control comparisons over every captured log value."""
    operations = {
        ">": lambda test, control: test > control,
        ">=": lambda test, control: test >= control,
        "<": lambda test, control: test < control,
        "<=": lambda test, control: test <= control,
        "==": lambda test, control: test == control,
        "!=": lambda test, control: test != control,
    }
    failures = []
    for comparison in comparisons:
        pattern, relation = re.compile(comparison["pattern"]), comparison["relation"]
        values = {}
        for role in ("control", "test"):
            captures = pattern.findall(logs[role])
            try:
                values[role] = [int(value, 16 if value.lower().startswith("0x") else 10)
                                for value in captures]
            except (AttributeError, ValueError):
                values[role] = []
        if not values["control"] or len(values["control"]) != len(values["test"]):
            failures.append(f"compare:{comparison['pattern']} count")
        elif not all(operations[relation](test, control) for control, test in
                     zip(values["control"], values["test"])):
            failures.append(f"compare:test {relation} control for {comparison['pattern']}")
    return failures


def dxt1_texture():
    """The smallest valid texture: a 4x4 DXT1 DDS."""
    header = struct.pack("<7I44x", 124, 0x81007, 4, 4, 8, 0, 0)
    pixel_format = struct.pack("<2I4s5I", 32, 4, b"DXT1", 0, 0, 0, 0, 0)
    return b"DDS " + header + pixel_format + struct.pack("<4I4x", 0x1000, 0, 0, 0) + bytes(8)


def repeat_topic(esm, topic, out, master="Morrowind.esm"):
    """A plugin repeating ESM's DIAL record for TOPIC with one new response, copied from the
    topic's first and renamed."""
    from tes3x.records import records, subrecords

    def record(tag, flags, body):
        return tag + struct.pack("<III", len(body), 0, flags) + body

    def subrecord(tag, value):
        return tag + struct.pack("<I", len(value)) + value

    dial = info = None
    for tag, flags, body in records(esm):
        if tag == b"DIAL":
            if dial:
                break
            name = dict(subrecords(body)).get(b"NAME", b"").rstrip(b"\0")
            if name.decode("cp1252").casefold() == topic.casefold():
                dial = record(tag, flags, body)
        elif tag == b"INFO" and dial:
            renamed = {b"INAM": b"tes3x_dialogue_merge\0", b"PNAM": b"\0", b"NNAM": b"\0"}
            info = record(tag, flags, b"".join(subrecord(t, renamed.get(t, v))
                                              for t, v in subrecords(body)))
            break
    if not dial or not info:
        raise TestError(f"{esm}: no topic {topic!r} with a response")
    hedr = (struct.pack("<fI", 1.2, 0) + b"tes3x".ljust(32, b"\0")
            + b"tes3x dialogue-merge test".ljust(256, b"\0") + struct.pack("<I", 2))
    header = (subrecord(b"HEDR", hedr) + subrecord(b"MAST", master.encode() + b"\0")
              + subrecord(b"DATA", struct.pack("<Q", os.path.getsize(esm))))
    Path(out).write_bytes(record(b"TES3", 0, header) + dial + info)


def make_fixture(fixture, esm, library):
    """A generated test mod in LIBRARY: a plugin appending FIXTURE's opcodes to a retail script,
    one repeating a retail topic, and a texture so packaging has an asset. Built from the user's
    own retail master."""
    folder = Path(library) / FIXTURE_MOD
    folder.mkdir(parents=True, exist_ok=True)
    if fixture.get("opcodes"):
        import tes3x.scriptasm as tes3x_scriptasm
        tes3x_scriptasm.build(str(esm), fixture.get("script", "Main"), fixture["opcodes"],
                              str(folder / "TES3X Test.esp"))
    if fixture.get("dialogue"):
        repeat_topic(esm, fixture["dialogue"], folder / "TES3X Dialogue.esp")
    if fixture.get("texture"):
        (folder / "Textures").mkdir(exist_ok=True)
        (folder / "Textures" / "tx_tes3x_test.dds").write_bytes(dxt1_texture())
    return folder


def game_test_profile(template, test, library=None):
    """The profile a game test builds: TEMPLATE with the test's overlay, and with the generated
    fixture as its only mod when LIBRARY holds one."""
    profile = json.loads(json.dumps(template))
    for section, values in test.get("profile", {}).items():
        profile.setdefault(section, {}).update(values)
    if library:
        profile["profile"]["library"] = Path(library).as_posix()
        profile["mods"] = [{"name": FIXTURE_MOD, "enabled": True, "order": 10}]
        if profile.get("package", {}).get("mode", "retail") == "retail":
            profile.setdefault("package", {})["mode"] = "delta-bsa"
    return profile


def load_scenario(path):
    """A profile smoke test: a game test with a single build."""
    scenario = load_game_test(path)
    if scenario["kind"] != "single":
        raise TestError(f"{path}: profile tests must be single scenarios")
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
        if expression not in scenario.get("allow", []) and any(
                re.search(expression, line) for line in lines):
            failures.append("!" + expression)
    failures += assertion_failures("\n".join(lines), scenario["script"])
    failures += sequence_failures("\n".join(lines), scenario.get("sequence", {}).get("test", []))
    watch = re.compile(scenario.get("watch", r"exec[.>]|assert[.>]|diag\.|crash\.|hang\.|fatal\."))
    observed = [line.rstrip("\r") for line in lines if watch.search(line)]
    return not failures, failures, observed


def find_runner(value):
    """The command that runs xemu: the --runner script, else `tes3x xemu`."""
    if not value:
        return [sys.executable, "-m", "tes3x", "xemu"]
    path = Path(value).resolve()
    if not path.is_file():
        raise TestError(f"xemu runner not found: {path}")
    return [sys.executable, str(path)]


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
    config = local_config(getattr(args, "config", None)).resolve()
    workspace = data_dir().resolve()
    run_root = workspace / "build" / "xemu"
    work_root = Path(args.work_root).resolve()
    work_root.mkdir(parents=True, exist_ok=True)
    workspace.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    scenario_name = safe_name(scenario_path.stem)
    profile_name = profile["profile"]["name"]
    run_name = safe_name(f"profile-{profile_name}-{scenario_name}-{timestamp}")
    run_dir = run_root / run_name
    script = work_root / f"{run_name}.exec.txt"
    script.write_text(scenario["script"].strip() + "\n", encoding="ascii", newline="\n")

    command = [*runner, run_name, str(profile_path), "--work-root", str(workspace), "--direct-engine",
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
    env["TES3X_CONFIG"] = str(config)
    env["TES3X_DATA"] = str(data_dir().resolve())
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
    parser.add_argument("--scenario", default=GAME_TESTS / "smoke.toml",
                        help="single-run scenario TOML (default: tests/game/smoke.toml)")
    parser.add_argument("--runner", help="xemu runner script to use instead of tes3x xemu")
    parser.add_argument("--config", help="local TES3X config passed to the build pipeline")
    parser.add_argument("--work-root", default=data_dir() / "build" / "profile-tests",
                        help="small transient scripts/results (default: data folder's build/profile-tests)")
    parser.add_argument("--results", default=data_dir() / "profile-tests" / "results",
                        help="durable --record destination (default: data folder's profile-tests/results)")
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
                        help="argument passed to tes3x pipeline (repeatable)")
    args = parser.parse_args(argv)
    try:
        return run_library(args) if args.library_all else run_profile(args)
    except (TestError, ValueError, OSError, subprocess.SubprocessError) as exc:
        raise SystemExit(str(exc)) from exc


if __name__ == "__main__":
    raise SystemExit(main())
