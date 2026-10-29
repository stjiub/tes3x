"""The places the memory tour visits, and the vanilla baseline its free memory is measured against.

locations.toml lists them; `tes3x test PROFILE --tour cities` visits some and reports free
memory at each. memory-baseline.json holds the same tour on a vanilla build, written by
`--write-baseline`; the difference is what a mod list costs.
"""

import json
import re
import tomllib

from tes3x.paths import resource
from tes3x.tour import MARK, cells, last_session

LOCATIONS = resource("locations.toml")
BASELINE = resource("memory-baseline.json")
IDENTIFIER = re.compile(r"[a-z0-9][a-z0-9-]*\Z")
FIELDS = {"id", "group", "name", "cell", "grid", "refs", "tour"}


class LocationError(ValueError):
    pass


def load(path=LOCATIONS):
    """Locations in file order."""
    with open(path, "rb") as stream:
        raw = tomllib.load(stream)
    if raw.get("schema") != 1:
        raise LocationError(f"{path}: schema must be 1")
    result = {}
    for index, entry in enumerate(raw.get("location", []), 1):
        where = f"locations.toml: location[{index}]"
        if set(entry) - FIELDS:
            raise LocationError(f"{where} has unknown fields {sorted(set(entry) - FIELDS)}")
        if not isinstance(entry.get("id"), str) or not IDENTIFIER.fullmatch(entry["id"]):
            raise LocationError(f"{where}.id must use lowercase letters, digits and dash")
        if entry["id"] in result:
            raise LocationError(f"{where}: duplicate id {entry['id']}")
        for key in ("group", "name"):
            if not isinstance(entry.get(key), str) or not entry[key]:
                raise LocationError(f"{where}.{key} must be a non-empty string")
        if ("cell" in entry) == ("grid" in entry):
            raise LocationError(f"{where} needs exactly one of cell and grid")
        grid = entry.get("grid")
        if "grid" in entry and not (isinstance(grid, list) and len(grid) == 2
                                    and all(type(v) is int for v in grid)):
            raise LocationError(f"{where}.grid must be [x, y]")
        if "cell" in entry and not isinstance(entry["cell"], str):
            raise LocationError(f"{where}.cell must be a string")
        result[entry["id"]] = entry
    return result


def presets(locations):
    """{preset: [ids]} from the locations' tour fields."""
    out = {}
    for entry in locations.values():
        for name in entry.get("tour", []):
            out.setdefault(name, []).append(entry["id"])
    out["all"] = list(locations)
    return out


def select(locations, spec):
    """Ids named by SPEC: comma-separated preset names and location ids, in file order."""
    known = presets(locations)
    wanted = set()
    for item in (part.strip() for part in spec.split(",") if part.strip()):
        if item in known:
            wanted.update(known[item])
        elif item in locations:
            wanted.add(item)
        else:
            raise LocationError(f"unknown location or preset {item!r}; presets: "
                                + ", ".join(known))
    return [name for name in locations if name in wanted]


def command(entry):
    if "grid" in entry:
        return "coe %d, %d" % tuple(entry["grid"])
    return 'coc "%s"' % entry["cell"]


def script(locations, ids, wait=90):
    """Exec script lines that visit IDS and log free memory at each."""
    lines = ["mark start"]
    for name in ids:
        lines += [command(locations[name]), "wait %d" % wait, "mark " + name]
    return "\n".join(lines)


def missing(locations, esm_paths):
    """Locations whose cell is in none of the plugins."""
    found = cells(esm_paths)
    names = {key[1] for key in found if key[0] == "int"}
    grids = {value for key, value in found.items() if key[0] == "ext"}
    gone = []
    for entry in locations.values():
        if "grid" in entry:
            if tuple(entry["grid"]) not in grids:
                gone.append(entry["id"])
        elif entry["cell"].lower() not in names:
            gone.append(entry["id"])
    return gone


def measure(log_text):
    """{label: free KB} for the last session's marks, in order."""
    out = {}
    for line in last_session(log_text):
        match = MARK.match(line.strip())
        if match:
            out[match.group(2)] = int(match.group(3))
    return out


def load_baseline(path=BASELINE):
    try:
        with open(path, encoding="utf-8") as stream:
            return json.load(stream).get("free_kb", {})
    except (OSError, ValueError):
        return {}


def write_baseline(free, path=BASELINE):
    with open(path, "w", encoding="utf-8", newline="\n") as stream:
        json.dump({"schema": 1, "free_kb": free}, stream, indent=2)
        stream.write("\n")


def summary(locations, ids, free, baseline):
    """Rows and totals for a tour: free KB at each stop, and how much less than the baseline."""
    rows = []
    for name in ids:
        kb = free.get(name)
        base = baseline.get(name)
        rows.append({"id": name, "name": locations[name]["name"], "free_kb": kb,
                     "baseline_kb": base,
                     "extra_kb": None if kb is None or base is None else base - kb})
    seen = [row for row in rows if row["free_kb"] is not None]
    low = min(seen, key=lambda row: row["free_kb"]) if seen else None
    extra = [row["extra_kb"] for row in rows if row["extra_kb"] is not None]
    return {"rows": rows, "visited": len(seen), "stops": len(rows),
            "low": {"id": low["id"], "free_kb": low["free_kb"]} if low else None,
            "extra_kb": max(extra) if extra else None}


def table(result):
    lines = ["%-34s %9s %9s %9s" % ("location", "free KB", "vanilla", "extra KB")]
    for row in result["rows"]:
        lines.append("%-34s %9s %9s %9s" % (
            row["name"][:34], *("-" if row[key] is None else row[key]
                                for key in ("free_kb", "baseline_kb", "extra_kb"))))
    low = result["low"]
    lines.append("")
    lines.append("visited %d of %d" % (result["visited"], result["stops"]))
    if low:
        lines.append("lowest free memory: %d KB at %s" % (low["free_kb"], low["id"]))
    if result["extra_kb"] is not None:
        lines.append("most memory over vanilla: %d KB" % result["extra_kb"])
    return "\n".join(lines)
