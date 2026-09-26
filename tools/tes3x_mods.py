#!/usr/bin/env python3
"""Validate a manual mod compatibility ledger and generate its Markdown view."""

import argparse
from pathlib import Path
import re
import tomllib


STATUSES = {
    "untested", "testing", "works", "works-with-requirements", "broken", "not-possible",
}
IDENTIFIER = re.compile(r"[a-z0-9][a-z0-9._-]*\Z")


class ModLedgerError(ValueError):
    pass


def strings(value, field):
    if value is None:
        return []
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ModLedgerError(f"{field} must be an array of strings")
    return value


def load_ledger(path):
    try:
        with open(path, "rb") as stream:
            raw = tomllib.load(stream)
    except FileNotFoundError as exc:
        raise ModLedgerError(f"missing {path}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ModLedgerError(f"{path}: {exc}") from exc
    if raw.get("schema") != 1:
        raise ModLedgerError(f"{path}: schema must be 1")
    unknown = set(raw) - {"schema", "mod"}
    if unknown:
        raise ModLedgerError(f"{path}: unknown fields: {', '.join(sorted(unknown))}")
    rows = raw.get("mod", [])
    if not isinstance(rows, list):
        raise ModLedgerError(f"{path}: mod must be an array of tables")
    result = []
    ids = set()
    allowed = {"id", "name", "version", "url", "status", "requirements", "observations",
               "limitations", "validation", "notes"}
    for index, row in enumerate(rows, 1):
        where = f"mod[{index}]"
        if not isinstance(row, dict):
            raise ModLedgerError(f"{path}: {where} must be a table")
        extra = set(row) - allowed
        if extra:
            raise ModLedgerError(f"{path}: {where} has unknown fields: {', '.join(sorted(extra))}")
        mod_id = row.get("id")
        if not isinstance(mod_id, str) or not IDENTIFIER.fullmatch(mod_id):
            raise ModLedgerError(f"{path}: {where}.id is invalid")
        if mod_id in ids:
            raise ModLedgerError(f"{path}: duplicate mod id {mod_id}")
        ids.add(mod_id)
        for field in ("name", "version", "url", "observations", "limitations", "notes"):
            if field in row and not isinstance(row[field], str):
                raise ModLedgerError(f"{path}: {where}.{field} must be a string")
        if not row.get("name"):
            raise ModLedgerError(f"{path}: {where}.name is required")
        status = row.get("status", "untested")
        if status not in STATUSES:
            raise ModLedgerError(f"{path}: {where}.status must be one of {', '.join(sorted(STATUSES))}")
        for field in ("requirements", "validation"):
            strings(row.get(field), f"{where}.{field}")
        result.append(dict(row, status=status))
    return result


def cell(value):
    return str(value or "-").replace("|", "\\|").replace("\n", " ")


def render(rows, source_name):
    lines = [
        "# Mod compatibility",
        "",
        f"Generated from `{source_name}`. Status is a manual maintainer decision; automated test",
        "results are evidence for the named scenario and never change status automatically.",
        "",
        "| Mod | Version | Status | Requirements | Observation |",
        "|---|---|---|---|---|",
    ]
    for row in sorted(rows, key=lambda item: item["name"].casefold()):
        name = f"[{row['name']}]({row['url']})" if row.get("url") else row["name"]
        requirements = "; ".join(row.get("requirements", [])) or "-"
        lines.append("| " + " | ".join(cell(value) for value in (
            name, row.get("version", "unknown"), row["status"], requirements,
            row.get("observations", "-"))) + " |")
        details = []
        if row.get("limitations"):
            details.append("Limitations: " + row["limitations"])
        if row.get("notes"):
            details.append(row["notes"])
        if row.get("validation"):
            details.append("Evidence: " + ", ".join(f"[`{Path(value).name}`]({value})"
                                                     for value in row["validation"]))
        if details:
            lines += ["", f"### {row['name']}", "", "  ".join(details)]
    return "\n".join(lines) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("ledger", help="manual mods.toml source")
    parser.add_argument("--write", metavar="MARKDOWN", help="write generated Markdown here")
    args = parser.parse_args(argv)
    try:
        rows = load_ledger(Path(args.ledger))
        print(f"{len(rows)} mod compatibility entries")
        if args.write:
            output = Path(args.write)
            text = render(rows, Path(args.ledger).name)
            if output.is_file() and output.read_text(encoding="utf-8") == text:
                print(f"unchanged {output}")
            else:
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_text(text, encoding="utf-8", newline="\n")
                print(f"wrote {output}")
        return 0
    except (ModLedgerError, OSError) as exc:
        raise SystemExit(str(exc)) from exc


if __name__ == "__main__":
    raise SystemExit(main())
