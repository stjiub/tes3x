#!/usr/bin/env python3
"""Record and check proof that a patch works: a control run and a patched run, with their logs.

A record is patches/<patch>/<date>-<environment>.toml, in the patch's folder beside the logs it
cites. Each run's diagnostics log names the payload build and the patch mask that was live, so a
record shows that the patched run carried the patch and the control run did not.

  tes3x_proof.py record mcp-102 --env xemu --control RUN --patched RUN --watch "mcp102\\.loaded"
      --claim "..." --method "..."
  tes3x_proof.py check

RUN is a diagnostics log, or an xemu run folder holding tes3xlog.txt.
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
ENVIRONMENTS = ("xemu", "hardware")
RESULTS = ("pass", "fail")
ROLES = ("control", "patched")
RECORD_FIELDS = (("patch", "date", "environment", "platform", "result", "claim", "method", "run"),
                 ("not_established", "retail_xbe_sha1", "tes3x", "converted_from"))
RUN_FIELDS = (("role", "observed"), ("build", "patches", "log", "log_sha256"))
SCENARIO = "scenario.toml"
SCENARIO_FIELDS = ("claim", "method", "watch", "script", "expect")


class ProofError(ValueError):
    pass


def check_fields(entry, fields, where):
    required, optional = fields
    missing = [key for key in required if key not in entry]
    unknown = set(entry) - set(required) - set(optional)
    if missing or unknown:
        raise ProofError(f"{where}: missing {missing or 'nothing'}, "
                         f"unknown {sorted(unknown) or 'nothing'}")


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
    roles = [run.get("role") for run in record["run"]]
    if roles.count("patched") != 1 or roles.count("control") > 1 or set(roles) - set(ROLES):
        raise ProofError(f"{where}: needs one patched run and at most one control run")
    bit = patch.get("bit")
    for run in record["run"]:
        check_fields(run, RUN_FIELDS, f"{where} {run['role']} run")
        if "log" in run:
            log = path.parent / run["log"]
            if not log.is_file():
                raise ProofError(f"{where}: log {run['log']} is missing")
            if hashlib.sha256(log.read_bytes()).hexdigest() != run.get("log_sha256"):
                raise ProofError(f"{where}: log {run['log']} does not match its hash")
        if bit is not None and "patches" in run:
            carried = bool(int(run["patches"], 16) & (1 << bit))
            if carried != (run["role"] == "patched"):
                raise ProofError(f"{where}: the {run['role']} run's patch mask {run['patches']} "
                                 f"{'lacks' if carried is False else 'has'} {record['patch']}")
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


def read_run(source, watch):
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
        marker = folder / "pipeline" / MARKER
        if marker.is_file():
            meta = json.loads(marker.read_text(encoding="utf-8"))
            run.update({key: meta[key] for key in ("tes3x", "retail_xbe_sha1") if meta.get(key)})
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
    return run


def toml_value(value):
    if isinstance(value, datetime.date):
        return value.isoformat()
    if isinstance(value, list):
        return "[\n" + "".join(f"    {toml_value(item)},\n" for item in value) + "]"
    return json.dumps(value, ensure_ascii=False)


def record(args):
    if args.patch not in registry.BY_NAME:
        raise ProofError(f"unknown patch {args.patch!r}; see tools/tes3x_patch.py --list")
    watch = re.compile(args.watch)
    runs = [("control", read_run(args.control, watch))] if args.control else []
    runs.append(("patched", read_run(args.patched, watch)))
    patched = runs[-1][1]
    platform = args.platform or patched.get("platform")
    if not platform:
        raise ProofError("say where it ran with --platform, such as \"Xbox 1.4, 4627 BIOS\"")
    for role, run in runs:
        if not run["observed"]:
            raise ProofError(f"--watch matched nothing in the {role} log {run['log_path']}")

    date = datetime.date.fromisoformat(args.date) if args.date else datetime.date.today()
    folder = PATCH_DIRS / args.patch
    folder.mkdir(parents=True, exist_ok=True)
    stem, n = f"{date.isoformat()}-{args.env}", 1
    while (folder / f"{stem}.toml").exists():
        n += 1
        stem = f"{date.isoformat()}-{args.env}-{n}"

    head = {"patch": args.patch, "date": date, "environment": args.env, "platform": platform,
            "result": args.result, "claim": args.claim, "method": args.method}
    if args.not_established:
        head["not_established"] = args.not_established
    for key in ("retail_xbe_sha1", "tes3x"):
        if patched.get(key):
            head[key] = patched[key]
    lines = [f"{key} = {toml_value(value)}" for key, value in head.items()]
    for role, run in runs:
        log_name = f"{stem}-{role}.log"
        shutil.copyfile(run["log_path"], folder / log_name)
        digest = hashlib.sha256((folder / log_name).read_bytes()).hexdigest()
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
    rec.add_argument("--watch", required=True, metavar="REGEX",
                     help="log lines that show the result, such as 'mcp102\\.loaded'")
    rec.add_argument("--claim", required=True, help="what the runs show, in one sentence")
    rec.add_argument("--method", required=True, help="how to repeat the test")
    rec.add_argument("--not-established", help="what the runs do not show")
    rec.add_argument("--platform", help="where it ran (default: read from an xemu run folder)")
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
