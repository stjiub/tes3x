#!/usr/bin/env python3
"""Record and check repeatable validation runs for an implemented patch.

A patch's game test is tests/game/<patch>.toml. Results are local files under
build/validation/patches/<patch>/ by default; --results selects another store. Schema 3 writes
hashed provenance with sanitized commands, inputs and platform details. A single test exercises
one build; a comparison test runs a control and a test build.

  tes3x validate record mcp-102 --env xemu --control RUN --test RUN
  tes3x validate check
  tes3x validate --results PATH check --gate
  tes3x validate status

RUN is a diagnostics log, or an xemu run folder holding tes3xlog.txt and .tes3x-run.json. Results
record observations; they do not change a patch's channel. `check --gate` also fails while a
preview or release patch has no pass against its current game test.
"""

import argparse
import datetime
import hashlib
import json
from pathlib import Path
import re
import shutil
import tomllib

import tes3x.patches as registry
from tes3x.paths import checkout
from tes3x.diag import assertion_failures, latest_values, parse_log
from tes3x.test import (GAME_TESTS, comparison_failures, game_test_problems,
                        sequence_failures)

VALIDATION = checkout("build", "validation")
PATCH_DIRS = VALIDATION / "patches"

GATED_CHANNELS = ("preview", "release")

MARKER = ".tes3x-pipeline.json"
RUN_MARKER = ".tes3x-run.json"
ENVIRONMENTS = ("xemu", "hardware")
RESULTS = ("pass", "fail")
ROLES = ("control", "test")
LEGACY_ROLES = ("control", "patched")
KINDS = ("single", "comparison")
RECORD_FIELDS = (("patch", "date", "environment", "platform", "result", "run"),
                 ("claim", "method", "not_established", "retail_xbe_sha1", "tes3x",
                  "converted_from", "schema", "recorded_at",
                  "provenance", "provenance_sha256", "kind", "scenario",
                  "scenario_sha256", "purpose", "procedure", "limitations"))
RUN_FIELDS = (("role", "observed"), ("build", "patches", "log", "log_sha256"))
DEFAULT_WATCH = r"exec[.>]|assert[.>]|diag\.|crash\.|hang\.|fatal\."
GLOBAL_FAILURES = (r"crash\.", r"hang\.detected", r"fatal\.")
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


class ValidationError(ValueError):
    pass


def check_fields(entry, fields, where):
    required, optional = fields
    missing = [key for key in required if key not in entry]
    unknown = set(entry) - set(required) - set(optional)
    if missing or unknown:
        raise ValidationError(f"{where}: missing {missing or 'nothing'}, "
                         f"unknown {sorted(unknown) or 'nothing'}")


def scenario_path(patch):
    return GAME_TESTS / f"{patch}.toml"


def cited_scenarios(patch):
    """Current and historical paths a record may cite for the patch's game test."""
    return ((Path("tests") / "game" / f"{patch}.toml").as_posix(),
            (Path("tes3x") / "tests" / "game" / f"{patch}.toml").as_posix(),
            (Path("validation") / "patches" / patch / "scenario.toml").as_posix())


def relative(path):
    """A display path that never exposes an absolute workstation path."""
    path = Path(path).resolve()
    for root, prefix in ((checkout(), ""), (VALIDATION.resolve(), "results"),
                         (Path.cwd().resolve(), "")):
        if path.is_relative_to(root):
            rel = path.relative_to(root).as_posix()
            return f"{prefix}/{rel}" if prefix else rel
    return path.name


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


def validate_provenance_v2(data, patch, environment, result, where):
    if data.get("schema") != 2 or data.get("patch") != patch:
        raise ValidationError(f"{where}: provenance must be schema 2 for {patch}")
    if data.get("environment") != environment:
        raise ValidationError(f"{where}: provenance environment does not match the record")
    if not isinstance(data.get("inputs"), dict):
        raise ValidationError(f"{where}: provenance inputs are missing")
    platform = data.get("platform")
    if not isinstance(platform, dict) or platform.get("kind") != environment:
        raise ValidationError(f"{where}: provenance platform is missing or invalid")
    inputs = data["inputs"]
    for key, expected in PIPELINE_INPUT_REQUIRED.items():
        if type(inputs.get(key)) is not expected:
            raise ValidationError(f"{where}: provenance input {key} is missing or invalid")
    if inputs["schema"] != 2:
        raise ValidationError(f"{where}: provenance cites an unsupported pipeline schema")
    for key, digits in (("profile_sha256", 64), ("data_files_sha256", 64),
                        ("retail_xbe_sha1", 40)):
        if not is_digest(inputs[key], digits):
            raise ValidationError(f"{where}: provenance input {key} is not a digest")
    ini = inputs["ini"]
    if not is_digest(ini.get("base_sha256"), 64) \
            or not is_digest(ini.get("staged_sha256"), 64) \
            or not isinstance(ini.get("overrides"), list):
        raise ValidationError(f"{where}: provenance INI identity is incomplete")
    if environment == "xemu":
        unknown = set(platform) - {"kind", "version", "bios", "guest_ram_mb"}
        if unknown:
            raise ValidationError(f"{where}: unknown xemu platform fields {sorted(unknown)}")
        for key, expected in {"version": str, "bios": str, "guest_ram_mb": int}.items():
            if type(platform.get(key)) is not expected:
                raise ValidationError(f"{where}: xemu platform {key} is missing or invalid")
        if platform["guest_ram_mb"] <= 0 or not platform["version"] or not platform["bios"]:
            raise ValidationError(f"{where}: xemu platform values are invalid")
    else:
        unknown = set(platform) - {"kind"} - HARDWARE_REQUIRED.keys() - HARDWARE_OPTIONAL.keys()
        if unknown:
            raise ValidationError(f"{where}: unknown hardware platform fields {sorted(unknown)}")
        for key, expected in HARDWARE_REQUIRED.items():
            if type(platform.get(key)) is not expected:
                raise ValidationError(f"{where}: hardware platform {key} is missing or invalid")
        if platform["title_ram_mb"] > platform["installed_ram_mb"]:
            raise ValidationError(f"{where}: title-visible RAM exceeds installed RAM")
    runs = data.get("runs")
    if not isinstance(runs, list) or [run.get("role") for run in runs] != list(LEGACY_ROLES):
        raise ValidationError(f"{where}: provenance needs control and patched runs in order")
    for run in runs:
        if not isinstance(run.get("patches"), list) \
                or not isinstance(run.get("build_command"), list) \
                or not isinstance(run.get("run_command"), list) \
                or not is_digest(run.get("morrowind_xbe_sha256"), 64):
            raise ValidationError(f"{where}: {run.get('role', 'unknown')} run provenance is incomplete")
    control, patched = runs
    control_patches = patch_names(control.get("patches", []))
    patched_patches = patch_names(patched.get("patches", []))
    if control_patches - patched_patches or patched_patches - control_patches != {patch}:
        raise ValidationError(f"{where}: build patch lists must differ only by {patch}")
    revision = inputs.get("tes3x")
    if result == "pass" and (not revision or revision.endswith("-dirty")):
        raise ValidationError(f"{where}: a legacy schema 2 passing result needs a clean TES3X revision")


def validate_provenance_v3(data, patch, environment, result, kind, where):
    """Validate provenance for a single-run or comparison validation result."""
    if data.get("schema") != 3 or data.get("patch") != patch:
        raise ValidationError(f"{where}: provenance must be schema 3 for {patch}")
    if data.get("environment") != environment or data.get("kind") != kind:
        raise ValidationError(f"{where}: provenance does not match the validation result")
    if not isinstance(data.get("inputs"), dict):
        raise ValidationError(f"{where}: provenance inputs are missing")
    platform = data.get("platform")
    if not isinstance(platform, dict) or platform.get("kind") != environment:
        raise ValidationError(f"{where}: provenance platform is missing or invalid")
    inputs = data["inputs"]
    for key, expected in PIPELINE_INPUT_REQUIRED.items():
        if type(inputs.get(key)) is not expected:
            raise ValidationError(f"{where}: provenance input {key} is missing or invalid")
    if inputs["schema"] != 2:
        raise ValidationError(f"{where}: provenance cites an unsupported pipeline schema")
    for key, digits in (("profile_sha256", 64), ("data_files_sha256", 64),
                        ("retail_xbe_sha1", 40)):
        if not is_digest(inputs[key], digits):
            raise ValidationError(f"{where}: provenance input {key} is not a digest")
    ini = inputs["ini"]
    if not is_digest(ini.get("base_sha256"), 64) \
            or not is_digest(ini.get("staged_sha256"), 64) \
            or not isinstance(ini.get("overrides"), list):
        raise ValidationError(f"{where}: provenance INI identity is incomplete")
    if environment == "xemu":
        unknown = set(platform) - {"kind", "version", "bios", "guest_ram_mb"}
        if unknown:
            raise ValidationError(f"{where}: unknown xemu platform fields {sorted(unknown)}")
        for key, expected in {"version": str, "bios": str, "guest_ram_mb": int}.items():
            if type(platform.get(key)) is not expected:
                raise ValidationError(f"{where}: xemu platform {key} is missing or invalid")
        if platform["guest_ram_mb"] <= 0 or not platform["version"] or not platform["bios"]:
            raise ValidationError(f"{where}: xemu platform values are invalid")
    else:
        unknown = set(platform) - {"kind"} - HARDWARE_REQUIRED.keys() - HARDWARE_OPTIONAL.keys()
        if unknown:
            raise ValidationError(f"{where}: unknown hardware platform fields {sorted(unknown)}")
        for key, expected in HARDWARE_REQUIRED.items():
            if type(platform.get(key)) is not expected:
                raise ValidationError(f"{where}: hardware platform {key} is missing or invalid")
        if platform["title_ram_mb"] > platform["installed_ram_mb"]:
            raise ValidationError(f"{where}: title-visible RAM exceeds installed RAM")
    runs = data.get("runs")
    expected_roles = ["test"] if kind == "single" else ["control", "test"]
    if not isinstance(runs, list) or [run.get("role") for run in runs] != expected_roles:
        raise ValidationError(f"{where}: {kind} provenance needs {', '.join(expected_roles)} runs")
    for run in runs:
        if not isinstance(run.get("patches"), list) \
                or not isinstance(run.get("build_command"), list) \
                or not isinstance(run.get("run_command"), list) \
                or not is_digest(run.get("morrowind_xbe_sha256"), 64):
            raise ValidationError(f"{where}: {run.get('role', 'unknown')} run provenance is incomplete")
    if kind == "comparison":
        control, test = runs
        control_patches = patch_names(control.get("patches", []))
        test_patches = patch_names(test.get("patches", []))
        if control_patches - test_patches or test_patches - control_patches != {patch}:
            raise ValidationError(f"{where}: build patch lists must differ only by {patch}")
    elif patch not in patch_names(runs[0].get("patches", [])):
        raise ValidationError(f"{where}: test build does not include {patch}")
    revision = inputs.get("tes3x")
    if result == "pass" and (not revision or revision.endswith("-dirty")):
        raise ValidationError(f"{where}: a passing validation result needs a clean TES3X revision")


def load_hardware(path):
    with open(path, "rb") as stream:
        doc = tomllib.load(stream)
    hardware = doc.get("hardware", doc)
    if not isinstance(hardware, dict):
        raise ValidationError(f"{path}: hardware must be a TOML table")
    unknown = set(hardware) - HARDWARE_REQUIRED.keys() - HARDWARE_OPTIONAL.keys()
    missing = set(HARDWARE_REQUIRED) - set(hardware)
    if missing or unknown:
        raise ValidationError(f"{path}: missing {sorted(missing) or 'nothing'}, "
                         f"unknown {sorted(unknown) or 'nothing'}")
    for key, expected in {**HARDWARE_REQUIRED, **HARDWARE_OPTIONAL}.items():
        if key in hardware and type(hardware[key]) is not expected:
            raise ValidationError(f"{path}: hardware.{key} must be {expected.__name__}")
    if hardware["installed_ram_mb"] <= 0 or hardware["title_ram_mb"] <= 0 \
            or hardware["cpu_mhz"] <= 0:
        raise ValidationError(f"{path}: RAM and CPU values must be greater than zero")
    if hardware["title_ram_mb"] > hardware["installed_ram_mb"]:
        raise ValidationError(f"{path}: title-visible RAM cannot exceed installed RAM")
    if any(not hardware[key].strip() for key, kind in HARDWARE_REQUIRED.items() if kind is str):
        raise ValidationError(f"{path}: hardware strings cannot be empty")
    return hardware


def load_record(path):
    """A validated record. Logs it cites must match their hashes."""
    path = Path(path)
    where = relative(path)
    with open(path, "rb") as stream:
        record = tomllib.load(stream)
    check_fields(record, RECORD_FIELDS, where)
    patch = registry.BY_NAME.get(record["patch"])
    if patch is None:
        raise ValidationError(f"{where}: unknown patch {record['patch']!r}")
    if record["environment"] not in ENVIRONMENTS or record["result"] not in RESULTS:
        raise ValidationError(f"{where}: environment must be one of {ENVIRONMENTS}, "
                         f"result one of {RESULTS}")
    if not isinstance(record["date"], datetime.date):
        raise ValidationError(f"{where}: date must be a TOML date, such as 2026-09-21")
    if "recorded_at" in record and not isinstance(record["recorded_at"], datetime.datetime):
        raise ValidationError(f"{where}: recorded_at must be a TOML date-time")
    schema = record.get("schema", 1)
    if schema not in (1, 2, 3):
        raise ValidationError(f"{where}: unsupported validation schema {schema}")
    if schema in (1, 2):
        missing = [key for key in ("claim", "method") if key not in record]
        if missing:
            raise ValidationError(f"{where}: legacy result is missing {missing}")
    else:
        missing = [key for key in ("kind", "scenario", "scenario_sha256", "purpose",
                                   "procedure") if key not in record]
        if missing:
            raise ValidationError(f"{where}: schema 3 result is missing {missing}")
        if record["kind"] not in KINDS:
            raise ValidationError(f"{where}: kind must be one of {KINDS}")
        if record["scenario"] not in cited_scenarios(record["patch"]):
            raise ValidationError(f"{where}: scenario must be {cited_scenarios(record['patch'])[0]}")
        if not is_digest(record["scenario_sha256"], 64):
            raise ValidationError(f"{where}: scenario_sha256 is not a digest")
    if schema in (2, 3):
        for key in ("provenance", "provenance_sha256"):
            if key not in record:
                raise ValidationError(f"{where}: schema {schema} result is missing {key}")
        provenance_path = path.parent / record["provenance"]
        if not provenance_path.is_file():
            raise ValidationError(f"{where}: provenance {record['provenance']} is missing")
        if sha256_file(provenance_path) != record["provenance_sha256"]:
            raise ValidationError(f"{where}: provenance {record['provenance']} does not match its hash")
        try:
            provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ValidationError(f"{where}: invalid provenance: {exc}") from exc
        if schema == 2:
            validate_provenance_v2(provenance, record["patch"], record["environment"],
                                   record["result"], where)
        else:
            validate_provenance_v3(provenance, record["patch"], record["environment"],
                                   record["result"], record["kind"], where)
    roles = [run.get("role") for run in record["run"]]
    if schema == 2 and roles != list(LEGACY_ROLES):
        raise ValidationError(f"{where}: schema 2 needs control and patched runs in order")
    if schema == 3:
        expected = ["test"] if record["kind"] == "single" else list(ROLES)
        if roles != expected:
            raise ValidationError(f"{where}: {record['kind']} result needs {expected} runs in order")
    if schema == 1 and (roles.count("patched") != 1 or roles.count("control") > 1
                        or set(roles) - set(LEGACY_ROLES)):
        raise ValidationError(f"{where}: needs one patched run and at most one control run")
    bit = patch.get("bit")
    masks = {}
    for run in record["run"]:
        check_fields(run, RUN_FIELDS, f"{where} {run['role']} run")
        if schema in (2, 3):
            missing = [key for key in ("build", "patches", "log", "log_sha256")
                       if key not in run]
            if missing:
                raise ValidationError(f"{where}: {run['role']} run is missing {missing}")
        if "log" in run:
            log = path.parent / run["log"]
            if not log.is_file():
                raise ValidationError(f"{where}: log {run['log']} is missing")
            if hashlib.sha256(log.read_bytes()).hexdigest() != run.get("log_sha256"):
                raise ValidationError(f"{where}: log {run['log']} does not match its hash")
        if bit is not None and "patches" in run:
            masks[run["role"]] = int(run["patches"], 16)
            carried = bool(masks[run["role"]] & (1 << bit))
            if carried != (run["role"] in {"patched", "test"}):
                raise ValidationError(f"{where}: the {run['role']} run's patch mask {run['patches']} "
                                 f"{'lacks' if carried is False else 'has'} {record['patch']}")
    compared = schema == 2 and set(masks) == set(LEGACY_ROLES) \
        or schema == 3 and record["kind"] == "comparison" and set(masks) == set(ROLES)
    tested_role = "patched" if schema == 2 else "test"
    if compared and bit is not None and masks["control"] ^ masks[tested_role] != 1 << bit:
        raise ValidationError(f"{where}: runtime patch masks differ by more than {record['patch']}")
    return record


def check_scenario(path):
    """Problems with a patch's game test."""
    where = relative(path)
    try:
        with open(path, "rb") as f:
            spec = tomllib.load(f)
    except tomllib.TOMLDecodeError as exc:
        return [f"{where}: {exc}"]
    return game_test_problems(spec, where)


def load_records():
    """{path: record} for every result, and the problems with those that do not load."""
    records, problems = {}, []
    for path in sorted(PATCH_DIRS.rglob("*.toml")) if PATCH_DIRS.is_dir() else []:
        try:
            records[relative(path)] = load_record(path)
        except (ValidationError, tomllib.TOMLDecodeError) as exc:
            problems.append(str(exc))
    return records, problems


def check_all():
    """Every problem with the game tests and recorded results."""
    problems = []
    for path in sorted(GAME_TESTS.glob("*.toml")):
        if path.stem != "smoke":
            problems += check_scenario(path)
    return problems + load_records()[1]


def standing(patch, records):
    """(state, latest passing record) for a patch: current, stale, legacy or none. Current means a
    pass against the game test as it is now; stale, a pass against an older version of it; legacy,
    a pass from before results cited a game test."""
    passes = sorted((r for r in records.values() if r["patch"] == patch and r["result"] == "pass"),
                    key=lambda r: r["date"])
    test = scenario_path(patch)
    digest = sha256_file(test) if test.is_file() else None

    def state(record):
        if record.get("schema", 1) < 3:
            return "legacy"
        return "current" if record["scenario_sha256"] == digest else "stale"

    for wanted in ("current", "stale", "legacy"):
        found = [record for record in passes if state(record) == wanted]
        if found:
            return wanted, found[-1]
    return "none", None


def latest_result(patch, records):
    """The newest result for a patch. New records have an exact UTC time; old same-day records
    fall back to their numeric filename suffix and then path."""
    candidates = [(path, record) for path, record in records.items() if record["patch"] == patch]
    if not candidates:
        return None

    def order(item):
        path, record = item
        created = record.get("recorded_at")
        match = re.search(r"-(\d+)$", Path(path).stem)
        sequence = int(match.group(1)) if match else 1
        timestamp = created.astimezone(datetime.timezone.utc).isoformat() if created else ""
        return (record["date"].isoformat(), bool(created), timestamp, sequence, str(path))

    return max(candidates, key=order)[1]


def gate(records):
    """(failures, warnings) for patches in a gated channel."""
    failures, warnings = [], []
    for entry in registry.PATCHES:
        if entry["channel"] not in GATED_CHANNELS:
            continue
        state, _record = standing(entry["name"], records)
        where = f"{entry['name']} ({entry['channel']})"
        if state == "none":
            failures.append(f"{where}: no passing result")
        elif state == "stale":
            failures.append(f"{where}: its game test changed since the last pass")
        elif state == "legacy":
            test = scenario_path(entry["name"])
            warnings.append(f"{where}: passed only before game tests; "
                            f"{'rerun' if test.is_file() else 'write'} {relative(test)}")
    return failures, warnings


def status(records):
    rows = [("patch", "channel", "standing", "last result", "last pass", "test exists")]
    for entry in registry.PATCHES:
        state, record = standing(entry["name"], records)
        latest = latest_result(entry["name"], records)
        result = f"{latest['date']} {latest['result']} {latest['environment']}" if latest else "-"
        passed = f"{record['date']} {record['environment']}" if record else "-"
        rows.append((entry["name"], entry["channel"], state, result, passed,
                     "yes" if scenario_path(entry["name"]).is_file() else "-"))
    widths = [max(len(str(row[i])) for row in rows) for i in range(len(rows[0]))]
    for row in rows:
        print("  ".join(str(value).ljust(width) for value, width in zip(row, widths)).rstrip())


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
        raise ValidationError(f"{path}: invalid JSON: {exc}") from exc


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
        mask = values["diag.patches"] | (values.get("diag.patches_hi", 0) << 32)
        run["patches"] = "0x%0*X" % (16 if mask >> 32 else 8, mask)
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


def expectation_failures(log_path, expectations, script="", allow=(), sequence=()):
    """Unmet patterns, failed asserts and global failure lines in one run log."""
    text = log_path.read_text(encoding="latin-1")
    lines = text.splitlines()
    failures = []
    for item in expectations:
        negate = item.startswith("!")
        pattern = item[1:] if negate else item
        hit = next((line for line in lines if re.search(pattern, line)), None)
        if (hit is None) != negate:
            failures.append(item)
    for pattern in GLOBAL_FAILURES:
        if pattern not in allow and any(re.search(pattern, line) for line in lines):
            failures.append("!" + pattern)
    return failures + assertion_failures(text, script) + sequence_failures(text, sequence)


def extra_fixtures(items):
    result = []
    for item in items:
        label, separator, value = item.partition("=")
        if not separator or not label or not value:
            raise ValidationError("--fixture wants LABEL=PATH")
        path = Path(value)
        if not path.is_file():
            raise ValidationError(f"fixture not found: {path}")
        result.append({"label": label, "name": path.name, "size": path.stat().st_size,
                       "sha256": sha256_file(path)})
    return result


def build_provenance(patch, environment, runs, kind="comparison", hardware=None, fixtures=()):
    expected_roles = ["test"] if kind == "single" else list(ROLES)
    if [role for role, _run in runs] != expected_roles:
        raise ValidationError(f"{kind} validation needs {', '.join(expected_roles)} runs")
    metas = []
    for role, run in runs:
        meta = run.get("pipeline")
        if not isinstance(meta, dict) or meta.get("schema") != 2:
            raise ValidationError(f"the {role} run has no schema 2 pipeline marker")
        metas.append(meta)
    if kind == "comparison":
        if pipeline_inputs(metas[0]) != pipeline_inputs(metas[1]):
            raise ValidationError("control and test build inputs differ")
        control_patches, test_patches = patch_names(metas[0].get("patches", [])), \
            patch_names(metas[1].get("patches", []))
        if control_patches - test_patches or test_patches - control_patches != {patch}:
            raise ValidationError(f"control and test build patch lists must differ only by {patch}")
    elif patch not in patch_names(metas[0].get("patches", [])):
        raise ValidationError(f"test build does not include {patch}")

    if environment == "xemu":
        details = [run.get("platform_details") for _role, run in runs]
        if not all(isinstance(item, dict) and item.get("kind") == "xemu" for item in details):
            raise ValidationError("validation needs .tes3x-run.json from every xemu run")
        if any(item != details[0] for item in details[1:]):
            raise ValidationError("control and test xemu platforms differ")
        run_fixtures = [(run.get("run_meta") or {}).get("fixtures", {}) for _role, run in runs]
        if any(item != run_fixtures[0] for item in run_fixtures[1:]):
            raise ValidationError("control and test fixtures differ")
        platform, common_fixtures = details[0], run_fixtures[0]
    else:
        if hardware is None:
            raise ValidationError("hardware validation needs --hardware-config")
        platform, common_fixtures = {"kind": "hardware", **hardware}, {}
    if fixtures:
        common_fixtures = {**common_fixtures, "additional": list(fixtures)}

    common = pipeline_inputs(metas[0])
    common["fixtures"] = common_fixtures
    validation_runs = []
    for (role, run), meta in zip(runs, metas):
        run_meta = run.get("run_meta") or {}
        validation_runs.append({
            "role": role,
            "patches": meta["patches"],
            "build_command": meta.get("command", []),
            "run_command": run_meta.get("command", []),
            "morrowind_xbe_sha256": run_meta.get(
                "morrowind_xbe_sha256", meta.get("morrowind_xbe_sha256")),
        })
    return {"schema": 3, "patch": patch, "environment": environment, "kind": kind,
            "platform": platform, "inputs": common, "runs": validation_runs}


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
        raise ValidationError(f"unknown patch {args.patch!r}; see tes3x/tes3x patch --list")
    if args.control_build and not args.control:
        raise ValidationError("--control-build needs --control")
    if args.hardware_config and args.env != "hardware":
        raise ValidationError("--hardware-config is only for --env hardware")
    scenario_file = scenario_path(args.patch).resolve()
    if args.scenario and Path(args.scenario).resolve() != scenario_file:
        raise ValidationError(f"--scenario must be {relative(scenario_file)}")
    if not scenario_file.is_file():
        raise ValidationError(f"no game test: {relative(scenario_file)}")
    scenario_problems = check_scenario(scenario_file)
    if scenario_problems:
        raise ValidationError("; ".join(scenario_problems))
    with open(scenario_file, "rb") as stream:
        scenario = tomllib.load(stream)
    kind = scenario["kind"]
    if (kind == "comparison") != bool(args.control):
        need = "needs --control" if kind == "comparison" else "does not take --control"
        raise ValidationError(f"a {kind} scenario {need}")
    watch = re.compile(scenario.get("watch", DEFAULT_WATCH))
    runs = [("control", read_run(args.control, watch, args.control_build))] \
        if args.control else []
    runs.append(("test", read_run(args.test, watch, args.test_build)))
    tested = runs[-1][1]
    for role, run in runs:
        if args.result == "pass" and not run["observed"]:
            raise ValidationError(f"--watch matched nothing in the {role} log {run['log_path']}")
        if args.result == "pass":
            failures = expectation_failures(run["log_path"], scenario["expect"][role],
                                            scenario["script"], scenario.get("allow", []),
                                            scenario.get("sequence", {}).get(role, []))
            if failures:
                raise ValidationError(f"{role} run failed expectations: {', '.join(failures)}")
    if args.result == "pass" and kind == "comparison":
        logs = {role: run["log_path"].read_text(encoding="latin-1") for role, run in runs}
        failures = comparison_failures(logs, scenario.get("compare", []))
        if failures:
            raise ValidationError("comparison failed: " + ", ".join(failures))

    hardware = load_hardware(args.hardware_config) if args.hardware_config else None
    provenance = build_provenance(args.patch, args.env, runs, kind, hardware,
                                  extra_fixtures(args.fixture))
    validate_provenance_v3(provenance, args.patch, args.env, args.result, kind, "new result")
    platform = platform_summary(provenance["platform"])

    date = datetime.date.fromisoformat(args.date) if args.date else datetime.date.today()
    folder = PATCH_DIRS / args.patch
    folder.mkdir(parents=True, exist_ok=True)
    stem, n = f"{date.isoformat()}-{args.env}", 1
    while (folder / f"{stem}.toml").exists():
        n += 1
        stem = f"{date.isoformat()}-{args.env}-{n}"

    scenario_rel = cited_scenarios(args.patch)[0]
    head = {"patch": args.patch, "date": date,
            "recorded_at": datetime.datetime.now(datetime.timezone.utc),
            "environment": args.env, "platform": platform,
            "result": args.result, "schema": 3, "kind": kind, "scenario": scenario_rel,
            "scenario_sha256": sha256_file(scenario_file), "purpose": scenario["purpose"],
            "procedure": scenario["procedure"]}
    if scenario.get("limitations"):
        head["limitations"] = scenario["limitations"]
    provenance_name = f"{stem}-provenance.json"
    provenance_path = folder / provenance_name
    provenance_path.write_text(json.dumps(provenance, indent=2, ensure_ascii=False,
                                          sort_keys=True) + "\n", encoding="utf-8")
    head.update({"provenance": provenance_name,
                 "provenance_sha256": sha256_file(provenance_path)})
    for key in ("retail_xbe_sha1", "tes3x"):
        if tested.get(key):
            head[key] = tested[key]
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
    except ValidationError:
        path.unlink()
        provenance_path.unlink()
        for log_copy in copied_logs:
            log_copy.unlink()
        raise
    print(f"wrote {relative(path)}")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--results", metavar="DIR",
                    help="local result store (default: build/validation)")
    sub = ap.add_subparsers(dest="command", required=True)
    rec = sub.add_parser("record", help="write a validation result from completed scenario runs")
    rec.add_argument("patch")
    rec.add_argument("--env", required=True, choices=ENVIRONMENTS)
    rec.add_argument("--scenario", help="the patch's game test (default: tests/game/PATCH.toml)")
    rec.add_argument("--test", required=True, metavar="RUN", help="log or xemu run folder")
    rec.add_argument("--control", metavar="RUN", help="control run for a comparison scenario")
    rec.add_argument("--test-build", metavar="PIPELINE",
                     help="hardware build folder or .tes3x-pipeline.json for the test run")
    rec.add_argument("--control-build", metavar="PIPELINE",
                     help="hardware build folder or .tes3x-pipeline.json for the control run")
    rec.add_argument("--hardware-config", metavar="TOML",
                     help="structured console identity for hardware validation")
    rec.add_argument("--fixture", action="append", default=[], metavar="LABEL=PATH",
                     help="hash an additional script, save or other test input (repeatable)")
    rec.add_argument("--result", choices=RESULTS, default="pass")
    rec.add_argument("--date", help="YYYY-MM-DD (default: today)")
    check = sub.add_parser("check", help="validate every game test and local result")
    check.add_argument("--gate", action="store_true",
                       help="also require current passes for preview and release patches")
    sub.add_parser("status", help="list each patch's channel and latest pass")
    args = ap.parse_args(argv)
    global VALIDATION, PATCH_DIRS
    if args.results:
        VALIDATION = Path(args.results).resolve()
        PATCH_DIRS = VALIDATION / "patches"
    try:
        if args.command == "record":
            record(args)
        elif args.command == "status":
            status(load_records()[0])
        else:
            problems = check_all()
            failures, warnings = gate(load_records()[0]) if args.gate else ([], [])
            for line in problems + failures + ["warning: " + w for w in warnings]:
                print(line)
            if problems or failures:
                raise SystemExit(1)
            print("all game tests and local records valid"
                  + ("; every gated patch has passed" if args.gate else ""))
    except ValidationError as exc:
        raise SystemExit(str(exc))


if __name__ == "__main__":
    main()
