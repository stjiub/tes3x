#!/usr/bin/env python3
"""Record and check proof that a patch works: a control run and a patched run, with their logs.

A record is patches/<patch>/<date>-<environment>.toml, in the patch's folder beside the logs it
cites. Schema 2 also writes a hashed provenance JSON with sanitized build commands, inputs and
platform details, and proves that the two builds differ only by the patch under test.

  tes3x_proof.py record mcp-102 --env xemu --control RUN --patched RUN --watch "mcp102\\.loaded"
      --claim "..." --method "..."
  tes3x_proof.py check

RUN is a diagnostics log, or an xemu run folder holding tes3xlog.txt and .tes3x-run.json.
"""

import argparse
import datetime
import hashlib
import json
from pathlib import Path
import re
import shutil
import sys
import tomllib

import tes3x_patches as registry
from tes3x_diag import latest_values, parse_log

ROOT = Path(__file__).resolve().parents[1]
PATCH_DIRS = ROOT / "patches"
MARKER = ".tes3x-pipeline.json"
RUN_MARKER = ".tes3x-run.json"
ENVIRONMENTS = ("xemu", "hardware")
RESULTS = ("pass", "fail")
ROLES = ("control", "patched")
RECORD_FIELDS = (("patch", "date", "environment", "platform", "result", "claim", "method", "run"),
                 ("not_established", "retail_xbe_sha1", "tes3x", "converted_from", "schema",
                  "provenance", "provenance_sha256"))
RUN_FIELDS = (("role", "observed"), ("build", "patches", "log", "log_sha256"))
SCENARIO = "scenario.toml"
SCENARIO_FIELDS = ("claim", "method", "watch", "script", "expect")
PIPELINE_VOLATILE = {"patches", "command", "morrowind_xbe_sha256"}
HARDWARE_REQUIRED = {
    "unit": str,
    "board_revision": str,
    "installed_ram_mb": int,
    "title_ram_mb": int,
    "cpu": str,
    "cpu_mhz": int,
    "bios": str,
}
HARDWARE_OPTIONAL = {"storage": str, "video_mode": str}
PIPELINE_INPUT_REQUIRED = {
    "schema": int,
    "profile": str,
    "profile_sha256": str,
    "preset": str,
    "package_mode": str,
    "ini": dict,
    "mods": list,
    "plugins": list,
    "data_files_sha256": str,
    "toolchain": dict,
    "deploy_tree": str,
    "tes3x": str,
    "retail_xbe_sha1": str,
}


class ProofError(ValueError):
    pass


def check_fields(entry, fields, where):
    required, optional = fields
    missing = [key for key in required if key not in entry]
    unknown = set(entry) - set(required) - set(optional)
    if missing or unknown:
        raise ProofError(f"{where}: missing {missing or 'nothing'}, "
                         f"unknown {sorted(unknown) or 'nothing'}")


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def pipeline_inputs(meta):
    return {key: value for key, value in meta.items() if key not in PIPELINE_VOLATILE}


def patch_names(specs):
    """Patch identities from marker specs such as profile=0x1234 or title=Test."""
    return {spec.partition("=")[0] for spec in specs}


def is_digest(value, digits):
    return isinstance(value, str) and len(value) == digits \
        and all(char in "0123456789abcdef" for char in value.lower())


def validate_provenance(data, patch, environment, result, where):
    if data.get("schema") != 2 or data.get("patch") != patch:
        raise ProofError(f"{where}: provenance must be schema 2 for {patch}")
    if data.get("environment") != environment:
        raise ProofError(f"{where}: provenance environment does not match the record")
    if not isinstance(data.get("inputs"), dict):
        raise ProofError(f"{where}: provenance inputs are missing")
    platform = data.get("platform")
    if not isinstance(platform, dict) or platform.get("kind") != environment:
        raise ProofError(f"{where}: provenance platform is missing or invalid")
    inputs = data["inputs"]
    for key, expected in PIPELINE_INPUT_REQUIRED.items():
        if type(inputs.get(key)) is not expected:
            raise ProofError(f"{where}: provenance input {key} is missing or invalid")
    if inputs["schema"] != 2:
        raise ProofError(f"{where}: provenance cites an unsupported pipeline schema")
    for key, digits in (("profile_sha256", 64), ("data_files_sha256", 64),
                        ("retail_xbe_sha1", 40)):
        if not is_digest(inputs[key], digits):
            raise ProofError(f"{where}: provenance input {key} is not a digest")
    ini = inputs["ini"]
    if not is_digest(ini.get("base_sha256"), 64) \
            or not is_digest(ini.get("staged_sha256"), 64) \
            or not isinstance(ini.get("overrides"), list):
        raise ProofError(f"{where}: provenance INI identity is incomplete")
    if environment == "xemu":
        unknown = set(platform) - {"kind", "version", "bios", "guest_ram_mb"}
        if unknown:
            raise ProofError(f"{where}: unknown xemu platform fields {sorted(unknown)}")
        for key, expected in {"version": str, "bios": str, "guest_ram_mb": int}.items():
            if type(platform.get(key)) is not expected:
                raise ProofError(f"{where}: xemu platform {key} is missing or invalid")
        if platform["guest_ram_mb"] <= 0 or not platform["version"] or not platform["bios"]:
            raise ProofError(f"{where}: xemu platform values are invalid")
    else:
        unknown = set(platform) - {"kind"} - HARDWARE_REQUIRED.keys() - HARDWARE_OPTIONAL.keys()
        if unknown:
            raise ProofError(f"{where}: unknown hardware platform fields {sorted(unknown)}")
        for key, expected in HARDWARE_REQUIRED.items():
            if type(platform.get(key)) is not expected:
                raise ProofError(f"{where}: hardware platform {key} is missing or invalid")
        if platform["title_ram_mb"] > platform["installed_ram_mb"]:
            raise ProofError(f"{where}: title-visible RAM exceeds installed RAM")
    runs = data.get("runs")
    if not isinstance(runs, list) or [run.get("role") for run in runs] != list(ROLES):
        raise ProofError(f"{where}: provenance needs control and patched runs in order")
    for run in runs:
        if not isinstance(run.get("patches"), list) \
                or not isinstance(run.get("build_command"), list) \
                or not isinstance(run.get("run_command"), list) \
                or not is_digest(run.get("morrowind_xbe_sha256"), 64):
            raise ProofError(f"{where}: {run.get('role', 'unknown')} run provenance is incomplete")
    control, patched = runs
    control_patches = patch_names(control.get("patches", []))
    patched_patches = patch_names(patched.get("patches", []))
    if control_patches - patched_patches or patched_patches - control_patches != {patch}:
        raise ProofError(f"{where}: build patch lists must differ only by {patch}")
    revision = inputs.get("tes3x")
    if result == "pass" and (not revision or revision.endswith("-dirty")):
        raise ProofError(f"{where}: a schema 2 passing proof needs a clean TES3X revision")


def load_hardware(path):
    with open(path, "rb") as stream:
        doc = tomllib.load(stream)
    hardware = doc.get("hardware", doc)
    if not isinstance(hardware, dict):
        raise ProofError(f"{path}: hardware must be a TOML table")
    unknown = set(hardware) - HARDWARE_REQUIRED.keys() - HARDWARE_OPTIONAL.keys()
    missing = set(HARDWARE_REQUIRED) - set(hardware)
    if missing or unknown:
        raise ProofError(f"{path}: missing {sorted(missing) or 'nothing'}, "
                         f"unknown {sorted(unknown) or 'nothing'}")
    for key, expected in {**HARDWARE_REQUIRED, **HARDWARE_OPTIONAL}.items():
        if key in hardware and type(hardware[key]) is not expected:
            raise ProofError(f"{path}: hardware.{key} must be {expected.__name__}")
    if hardware["installed_ram_mb"] <= 0 or hardware["title_ram_mb"] <= 0 \
            or hardware["cpu_mhz"] <= 0:
        raise ProofError(f"{path}: RAM and CPU values must be greater than zero")
    if hardware["title_ram_mb"] > hardware["installed_ram_mb"]:
        raise ProofError(f"{path}: title-visible RAM cannot exceed installed RAM")
    if any(not hardware[key].strip() for key, kind in HARDWARE_REQUIRED.items() if kind is str):
        raise ProofError(f"{path}: hardware strings cannot be empty")
    return hardware


def load_record(path):
    """A validated record. Logs it cites must match their hashes."""
    path = Path(path)
    where = path.relative_to(ROOT).as_posix() if path.is_relative_to(ROOT) else str(path)
    with open(path, "rb") as stream:
        record = tomllib.load(stream)
    check_fields(record, RECORD_FIELDS, where)
    patch = registry.BY_NAME.get(record["patch"])
    if patch is None:
        raise ProofError(f"{where}: unknown patch {record['patch']!r}")
    if record["environment"] not in ENVIRONMENTS or record["result"] not in RESULTS:
        raise ProofError(f"{where}: environment must be one of {ENVIRONMENTS}, "
                         f"result one of {RESULTS}")
    if not isinstance(record["date"], datetime.date):
        raise ProofError(f"{where}: date must be a TOML date, such as 2026-09-21")
    schema = record.get("schema", 1)
    if schema not in (1, 2):
        raise ProofError(f"{where}: unsupported proof schema {schema}")
    if schema == 2:
        for key in ("provenance", "provenance_sha256"):
            if key not in record:
                raise ProofError(f"{where}: schema 2 record is missing {key}")
        provenance_path = path.parent / record["provenance"]
        if not provenance_path.is_file():
            raise ProofError(f"{where}: provenance {record['provenance']} is missing")
        if sha256_file(provenance_path) != record["provenance_sha256"]:
            raise ProofError(f"{where}: provenance {record['provenance']} does not match its hash")
        try:
            provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ProofError(f"{where}: invalid provenance: {exc}") from exc
        validate_provenance(provenance, record["patch"], record["environment"],
                            record["result"], where)
    roles = [run.get("role") for run in record["run"]]
    if schema == 2 and roles != list(ROLES):
        raise ProofError(f"{where}: schema 2 needs control and patched runs in order")
    if schema == 1 and (roles.count("patched") != 1 or roles.count("control") > 1
                        or set(roles) - set(ROLES)):
        raise ProofError(f"{where}: needs one patched run and at most one control run")
    bit = patch.get("bit")
    masks = {}
    for run in record["run"]:
        check_fields(run, RUN_FIELDS, f"{where} {run['role']} run")
        if schema == 2:
            missing = [key for key in ("build", "patches", "log", "log_sha256")
                       if key not in run]
            if missing:
                raise ProofError(f"{where}: {run['role']} run is missing {missing}")
        if "log" in run:
            log = path.parent / run["log"]
            if not log.is_file():
                raise ProofError(f"{where}: log {run['log']} is missing")
            if hashlib.sha256(log.read_bytes()).hexdigest() != run.get("log_sha256"):
                raise ProofError(f"{where}: log {run['log']} does not match its hash")
        if bit is not None and "patches" in run:
            masks[run["role"]] = int(run["patches"], 16)
            carried = bool(masks[run["role"]] & (1 << bit))
            if carried != (run["role"] == "patched"):
                raise ProofError(f"{where}: the {run['role']} run's patch mask {run['patches']} "
                                 f"{'lacks' if carried is False else 'has'} {record['patch']}")
    if schema == 2 and bit is not None and set(masks) == set(ROLES) \
            and masks["control"] ^ masks["patched"] != 1 << bit:
        raise ProofError(f"{where}: runtime patch masks differ by more than {record['patch']}")
    return record


def check_scenario(path):
    """A scenario is the script and expectations a control and a patched run are checked against."""
    where = path.relative_to(ROOT).as_posix()
    try:
        with open(path, "rb") as f:
            spec = tomllib.load(f)
    except tomllib.TOMLDecodeError as exc:
        return [f"{where}: {exc}"]
    missing = [key for key in SCENARIO_FIELDS if key not in spec]
    if missing:
        return [f"{where}: missing {missing}"]
    problems = []
    for role, items in spec["expect"].items():
        if role not in ROLES:
            problems.append(f"{where}: expect.{role} is not one of {list(ROLES)}")
            continue
        for item in items:
            try:
                re.compile(item.removeprefix("!"))
            except re.error as exc:
                problems.append(f"{where}: expect.{role} {item!r}: {exc}")
    return problems


def check_all():
    """Every problem with the records and with the table's evidence for its statuses."""
    problems, records = [], {}
    for path in sorted(PATCH_DIRS.rglob("*.toml")) if PATCH_DIRS.is_dir() else []:
        if path.name == SCENARIO:
            problems += check_scenario(path)
            continue
        try:
            records[path.relative_to(ROOT).as_posix()] = load_record(path)
        except (ProofError, tomllib.TOMLDecodeError) as exc:
            problems.append(str(exc))
    for entry in registry.PATCHES:
        cited = []
        for evidence in entry.get("evidence", []):
            if not evidence.startswith("patches/"):
                continue
            record = records.get(evidence)
            if record is None:
                problems.append(f"{entry['name']}: evidence {evidence} is missing or invalid")
            elif record["patch"] != entry["name"]:
                problems.append(f"{entry['name']}: evidence {evidence} is for {record['patch']}")
            else:
                cited.append(record)
        passed = {record["environment"] for record in cited if record["result"] == "pass"}
        if entry["status"] == "verified-hardware" and "hardware" not in passed:
            problems.append(f"{entry['name']}: verified-hardware cites no passing hardware record")
        if entry["status"] == "verified-xemu" and not passed and not any(
                e.startswith("findings:") for e in entry.get("evidence", [])):
            problems.append(f"{entry['name']}: verified-xemu cites no passing record")
    return problems


def read_json_marker(source, names):
    if not source:
        return None
    source = Path(source)
    candidates = [source] if source.is_file() and source.suffix.lower() == ".json" else []
    if source.is_dir():
        candidates += [source / name for name in names]
    path = next((candidate for candidate in candidates if candidate.is_file()), None)
    if not path:
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ProofError(f"{path}: invalid JSON: {exc}") from exc


def read_run(source, watch, build_source=None):
    """What a run's log, pipeline marker and xemu output say about it."""
    source = Path(source)
    folder = source if source.is_dir() else None
    log = source / "tes3xlog.txt" if folder else source
    text = log.read_text(encoding="latin-1")
    values = latest_values(parse_log(text))
    run = {"log_path": log,
           "observed": [line.rstrip("\r") for line in text.splitlines() if watch.search(line)]}
    if isinstance(values.get("diag.build"), int):
        run["build"] = "0x%08X" % values["diag.build"]
    if isinstance(values.get("diag.patches"), int):
        run["patches"] = "0x%08X" % values["diag.patches"]
    if folder:
        run_meta = read_json_marker(folder, (RUN_MARKER,))
        if run_meta:
            run["run_meta"] = run_meta
            run["pipeline"] = run_meta.get("pipeline")
            if run_meta.get("platform"):
                run["platform_details"] = run_meta["platform"]
        if not run.get("pipeline"):
            run["pipeline"] = read_json_marker(folder, (MARKER, f"pipeline/{MARKER}"))
        err, out = folder / "xemu.err", folder / "xemu.out"
        version = re.search(r"xemu_version: (\S+)", err.read_text(errors="replace")) \
            if err.is_file() else None
        launch = out.read_text(errors="replace") if out.is_file() else ""
        bios, memory = re.search(r"-bios (\S+)", launch), re.search(r" -m (\d+)", launch)
        if version:
            run["platform"] = ", ".join(
                ["xemu " + version.group(1)]
                + ([Path(bios.group(1)).name] if bios else [])
                + ([memory.group(1) + " MB"] if memory else []))
    if build_source:
        run["pipeline"] = read_json_marker(build_source, (MARKER, f"pipeline/{MARKER}"))
    meta = run.get("pipeline") or {}
    run.update({key: meta[key] for key in ("tes3x", "retail_xbe_sha1") if meta.get(key)})
    return run


def extra_fixtures(items):
    result = []
    for item in items:
        label, separator, value = item.partition("=")
        if not separator or not label or not value:
            raise ProofError("--fixture wants LABEL=PATH")
        path = Path(value)
        if not path.is_file():
            raise ProofError(f"fixture not found: {path}")
        result.append({"label": label, "name": path.name, "size": path.stat().st_size,
                       "sha256": sha256_file(path)})
    return result


def build_provenance(patch, environment, runs, hardware=None, fixtures=()):
    if [role for role, _run in runs] != list(ROLES):
        raise ProofError("schema 2 proof needs both control and patched runs")
    metas = []
    for role, run in runs:
        meta = run.get("pipeline")
        if not isinstance(meta, dict) or meta.get("schema") != 2:
            raise ProofError(f"the {role} run has no schema 2 pipeline marker")
        metas.append(meta)
    if pipeline_inputs(metas[0]) != pipeline_inputs(metas[1]):
        raise ProofError("control and patched build inputs differ")
    control_patches, patched_patches = patch_names(metas[0].get("patches", [])), \
        patch_names(metas[1].get("patches", []))
    if control_patches - patched_patches or patched_patches - control_patches != {patch}:
        raise ProofError(f"control and patched build patch lists must differ only by {patch}")

    if environment == "xemu":
        details = [run.get("platform_details") for _role, run in runs]
        if not all(isinstance(item, dict) and item.get("kind") == "xemu" for item in details):
            raise ProofError("schema 2 xemu proof needs .tes3x-run.json from both runs")
        if details[0] != details[1]:
            raise ProofError("control and patched xemu platforms differ")
        run_fixtures = [(run.get("run_meta") or {}).get("fixtures", {}) for _role, run in runs]
        if run_fixtures[0] != run_fixtures[1]:
            raise ProofError("control and patched fixtures differ")
        platform, common_fixtures = details[0], run_fixtures[0]
    else:
        if hardware is None:
            raise ProofError("schema 2 hardware proof needs --hardware-config")
        platform, common_fixtures = {"kind": "hardware", **hardware}, {}
    if fixtures:
        common_fixtures = {**common_fixtures, "additional": list(fixtures)}

    common = pipeline_inputs(metas[0])
    common["fixtures"] = common_fixtures
    proof_runs = []
    for (role, run), meta in zip(runs, metas):
        run_meta = run.get("run_meta") or {}
        proof_runs.append({
            "role": role,
            "patches": meta["patches"],
            "build_command": meta.get("command", []),
            "run_command": run_meta.get("command", []),
            "morrowind_xbe_sha256": run_meta.get(
                "morrowind_xbe_sha256", meta.get("morrowind_xbe_sha256")),
        })
    return {"schema": 2, "patch": patch, "environment": environment,
            "platform": platform, "inputs": common, "runs": proof_runs}


def platform_summary(platform):
    if platform["kind"] == "xemu":
        return f"xemu {platform['version']}, {platform['bios']}, {platform['guest_ram_mb']} MB"
    cpu = f"{platform['cpu']} {platform['cpu_mhz']} MHz"
    return (f"Xbox {platform['board_revision']}, {platform['bios']}, "
            f"{platform['installed_ram_mb']} MB installed/"
            f"{platform['title_ram_mb']} MB visible, {cpu}")


def toml_value(value):
    if isinstance(value, datetime.date):
        return value.isoformat()
    if isinstance(value, list):
        return "[\n" + "".join(f"    {toml_value(item)},\n" for item in value) + "]"
    return json.dumps(value, ensure_ascii=False)


def record(args):
    if args.patch not in registry.BY_NAME:
        raise ProofError(f"unknown patch {args.patch!r}; see tools/tes3x_patch.py --list")
    if args.control_build and not args.control:
        raise ProofError("--control-build needs --control")
    if args.hardware_config and args.env != "hardware":
        raise ProofError("--hardware-config is only for --env hardware")
    if not args.legacy and args.platform:
        raise ProofError("--platform is only for --legacy; schema 2 derives it from provenance")
    if args.legacy and (args.hardware_config or args.fixture
                        or args.control_build or args.patched_build):
        raise ProofError("structured build, hardware and fixture inputs cannot be used with --legacy")
    watch = re.compile(args.watch)
    runs = [("control", read_run(args.control, watch, args.control_build))] \
        if args.control else []
    runs.append(("patched", read_run(args.patched, watch, args.patched_build)))
    patched = runs[-1][1]
    for role, run in runs:
        if not run["observed"]:
            raise ProofError(f"--watch matched nothing in the {role} log {run['log_path']}")

    provenance = None
    if args.legacy:
        platform = args.platform or patched.get("platform")
        if not platform:
            raise ProofError("legacy records need --platform when it cannot be read from xemu")
    else:
        hardware = load_hardware(args.hardware_config) if args.hardware_config else None
        provenance = build_provenance(args.patch, args.env, runs, hardware,
                                      extra_fixtures(args.fixture))
        validate_provenance(provenance, args.patch, args.env, args.result, "new record")
        platform = platform_summary(provenance["platform"])

    date = datetime.date.fromisoformat(args.date) if args.date else datetime.date.today()
    folder = PATCH_DIRS / args.patch
    folder.mkdir(parents=True, exist_ok=True)
    stem, n = f"{date.isoformat()}-{args.env}", 1
    while (folder / f"{stem}.toml").exists():
        n += 1
        stem = f"{date.isoformat()}-{args.env}-{n}"

    head = {"patch": args.patch, "date": date, "environment": args.env, "platform": platform,
            "result": args.result, "claim": args.claim, "method": args.method}
    provenance_path = None
    if provenance:
        provenance_name = f"{stem}-provenance.json"
        provenance_path = folder / provenance_name
        provenance_path.write_text(json.dumps(provenance, indent=2, ensure_ascii=False,
                                              sort_keys=True) + "\n", encoding="utf-8")
        head.update({"schema": 2, "provenance": provenance_name,
                     "provenance_sha256": sha256_file(provenance_path)})
    if args.not_established:
        head["not_established"] = args.not_established
    for key in ("retail_xbe_sha1", "tes3x"):
        if patched.get(key):
            head[key] = patched[key]
    lines = [f"{key} = {toml_value(value)}" for key, value in head.items()]
    copied_logs = []
    for role, run in runs:
        log_name = f"{stem}-{role}.log"
        log_copy = folder / log_name
        shutil.copyfile(run["log_path"], log_copy)
        copied_logs.append(log_copy)
        digest = sha256_file(log_copy)
        lines += ["", "[[run]]", f"role = {toml_value(role)}"]
        lines += [f"{key} = {toml_value(run[key])}" for key in ("build", "patches") if key in run]
        lines += [f"log = {toml_value(log_name)}", f"log_sha256 = {toml_value(digest)}",
                  f"observed = {toml_value(run['observed'])}"]
    path = folder / f"{stem}.toml"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    try:
        load_record(path)
    except ProofError:
        path.unlink()
        if provenance_path:
            provenance_path.unlink()
        for log_copy in copied_logs:
            log_copy.unlink()
        raise
    rel = path.relative_to(ROOT).as_posix()
    print(f"wrote {rel}")
    print(f"cite it in patches.toml: evidence = [\"{rel}\"], set the status, then run "
          "tools/tes3x_patches.py --write")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command", required=True)
    rec = sub.add_parser("record", help="write a proof record from a control and a patched run")
    rec.add_argument("patch")
    rec.add_argument("--env", required=True, choices=ENVIRONMENTS)
    rec.add_argument("--patched", required=True, metavar="RUN", help="log or xemu run folder")
    rec.add_argument("--control", metavar="RUN", help="the same test without the patch")
    rec.add_argument("--patched-build", metavar="PIPELINE",
                     help="hardware build folder or .tes3x-pipeline.json for the patched run")
    rec.add_argument("--control-build", metavar="PIPELINE",
                     help="hardware build folder or .tes3x-pipeline.json for the control run")
    rec.add_argument("--watch", required=True, metavar="REGEX",
                     help="log lines that show the result, such as 'mcp102\\.loaded'")
    rec.add_argument("--claim", required=True, help="what the runs show, in one sentence")
    rec.add_argument("--method", required=True, help="how to repeat the test")
    rec.add_argument("--not-established", help="what the runs do not show")
    rec.add_argument("--hardware-config", metavar="TOML",
                     help="structured console identity for a hardware proof")
    rec.add_argument("--fixture", action="append", default=[], metavar="LABEL=PATH",
                     help="hash an additional script, save or other test input (repeatable)")
    rec.add_argument("--platform", help="free-form platform for --legacy records")
    rec.add_argument("--legacy", action="store_true",
                     help="write the old result-only format without build provenance")
    rec.add_argument("--result", choices=RESULTS, default="pass")
    rec.add_argument("--date", help="YYYY-MM-DD (default: today)")
    sub.add_parser("check", help="validate every record and the table's evidence")
    args = ap.parse_args(argv)
    try:
        if args.command == "record":
            record(args)
        else:
            problems = check_all()
            for problem in problems:
                print(problem)
            if problems:
                raise SystemExit(1)
            print("all records valid")
    except ProofError as exc:
        raise SystemExit(str(exc))


if __name__ == "__main__":
    main()
