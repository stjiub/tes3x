"""Measure Morrowind save records, changed references, and inventories."""

import argparse
import collections
import os
import struct
import sys

sys.path.insert(0, __file__.rsplit("\\", 1)[0].rsplit("/", 1)[0])
from tes3x_records import records, subrecords  # noqa: E402

HEADER = 16


def analyse(path):
    by_tag = collections.Counter()
    bytes_by_tag = collections.Counter()
    subs = collections.Counter()
    accounted = 0

    for tag, _flags, data in records(path):
        by_tag[tag] += 1
        bytes_by_tag[tag] += len(data) + HEADER
        accounted += len(data) + HEADER
        if tag in (b"CELL", b"NPCC", b"CREC", b"CNTC"):
            for stag, sdata in subrecords(data):
                subs[(tag, stag)] += 1

    size = os.path.getsize(path)
    return dict(path=path, size=size, accounted=accounted, records=sum(by_tag.values()),
                by_tag=by_tag, bytes_by_tag=bytes_by_tag, subs=subs,
                frmr=subs[(b"CELL", b"FRMR")],
                npco=subs[(b"NPCC", b"NPCO")] + subs[(b"CREC", b"NPCO")]
                + subs[(b"CNTC", b"NPCO")])


def bindings(path):
    """Return the MAST list and changed-reference binding histogram."""
    masters = []
    hist = collections.Counter()
    for tag, _flags, data in records(path):
        if tag == b"TES3":
            for stag, sdata in subrecords(data):
                if stag == b"MAST":
                    masters.append(sdata.rstrip(bytes(1)).decode("latin-1"))
        elif tag == b"CELL":
            for stag, sdata in subrecords(data):
                if stag == b"FRMR" and len(sdata) >= 4:
                    hist[struct.unpack("<I", sdata[:4])[0] >> 24] += 1
    return masters, hist


def poison_references(path, out, cell_name=None, from_index=1, to_index=0xFF):
    """Copy a save while making selected references fail master-index resolution."""
    if os.path.abspath(path) == os.path.abspath(out):
        raise ValueError("output must differ from the source save")
    if not 0 <= from_index <= 0xFF or not 0 <= to_index <= 0xFF:
        raise ValueError("reference indices must fit one byte")

    data = bytearray(open(path, "rb").read())
    record_off = 0
    cells = 0
    patched = 0
    while record_off < len(data):
        if record_off + HEADER > len(data):
            raise ValueError(f"truncated record header at {record_off}")
        tag, size, _unknown, _flags = struct.unpack_from("<4sIII", data, record_off)
        body = record_off + HEADER
        end = body + size
        if end > len(data):
            raise ValueError(f"{tag!r} exceeds file at {record_off}")
        if tag == b"CELL":
            cells += 1
            wanted = cell_name is None
            if cell_name is not None:
                for stag, sdata in subrecords(bytes(data[body:end])):
                    if (stag == b"NAME"
                            and sdata.rstrip(bytes(1)).decode("latin-1").casefold()
                            == cell_name.casefold()):
                        wanted = True
                        break
            if not wanted:
                record_off = end
                continue
            sub_off = body
            while sub_off < end:
                if sub_off + 8 > end:
                    raise ValueError(f"truncated CELL subrecord at {sub_off}")
                stag, ssize = struct.unpack_from("<4sI", data, sub_off)
                value_off = sub_off + 8
                sub_end = value_off + ssize
                if sub_end > end:
                    raise ValueError(f"{stag!r} exceeds CELL at {sub_off}")
                if stag == b"FRMR" and ssize >= 4:
                    value = struct.unpack_from("<I", data, value_off)[0]
                    if value >> 24 == from_index:
                        value = (value & 0x00FFFFFF) | (to_index << 24)
                        struct.pack_into("<I", data, value_off, value)
                        patched += 1
                        break
                sub_off = sub_end
        record_off = end

    if not patched:
        where = f" in CELL {cell_name!r}" if cell_name is not None else ""
        raise ValueError(f"no CELL reference used mod index {from_index}{where}")
    with open(out, "wb") as stream:
        stream.write(data)
    return cells, patched


def poison_first_per_cell(path, out, from_index=1, to_index=0xFF):
    return poison_references(path, out, from_index=from_index, to_index=to_index)


def poison_first_in_cell(path, out, cell_name, from_index=1, to_index=0xFF):
    return poison_references(path, out, cell_name=cell_name,
                             from_index=from_index, to_index=to_index)


def report_bindings(paths):
    total = collections.Counter()
    orders = {}
    for path in paths:
        masters, hist = bindings(path)
        total.update(hist)
        orders.setdefault(tuple(masters), []).append(path)
        n = sum(hist.values())
        print(f"  {os.path.basename(path)[:34]:<34} {len(masters):>4} MAST {n:>6} FRMR "
              f"{hist[0]:>6} idx0")

    print()
    print(f"  {len(orders)} distinct MAST order(s) across {len(paths)} save(s)")
    order = max(orders, key=lambda k: len(orders[k]))
    n = sum(total.values())
    print()
    print(f"  {'index':>5} {'count':>8} {'share':>7}  plugin")
    for idx, count in sorted(total.items(), key=lambda kv: -kv[1]):
        if idx == 0:
            who = "created at runtime (or orphaned - indistinguishable)"
        elif idx <= len(order):
            who = order[idx - 1]
        else:
            who = "OUT OF RANGE for this MAST list"
        print(f"  {idx:>5} {count:>8} {100 * count / n:>6.1f}%  {who}")


# What a save holds, as rows of docs/multiplayer-coverage.md: a row with no difference between a
# checkpoint and its rebuild from the server's state alone needs no checkpoint.
ROWS = (
    "Identity", "Position, cell, heading", "Inventory and gold", "Equipped items",
    "Level and skills", "Player mobile (attributes, current stats, undecoded)",
    "Player data (PNAM, SNAM, undecoded)", "Known spells", "Active effects", "Journal", "Topics",
    "Factions", "Player-made records", "Map exploration", "Player globals (PC*)",
    "Kills, stolen items", "Misc player data",
    "Clock and game", "Weather", "Globals", "Global scripts", "Script locals",
    "Actors (changed)", "Containers", "Object state", "Base records changed", "Other",
)
ATTRIBUTES = ("Strength", "Intelligence", "Willpower", "Agility", "Speed", "Endurance",
              "Personality", "Luck")
SKILLS = ("Block", "Armorer", "MediumArmor", "HeavyArmor", "BluntWeapon", "LongBlade", "Axe",
          "Spear", "Athletics", "Enchant", "Destruction", "Alteration", "Illusion", "Conjuration",
          "Mysticism", "Restoration", "Alchemy", "Unarmored", "Security", "Sneak", "Acrobatics",
          "LightArmor", "ShortBlade", "Marksman", "Mercantile", "Speechcraft", "HandToHand")
NPDT = struct.Struct("<h8B27BxHHHBBBxi")  # a full (not auto-calculated) NPC_ NPDT, 52 bytes
FACTION = struct.Struct("<iiI32s")  # PCDT FNAM: rank, reputation, flags, faction
SCRIPT_SUBS = (b"SCRI", b"SLCS", b"SLSD", b"SLLD", b"SLFD")
ACTOR_SUBS = (b"ACDT", b"ACSC", b"ACSL", b"CHRD", b"CRED", b"WNAM", b"FGTN", b"AIDT", b"AI_W",
              b"AI_T", b"AI_F", b"AI_E", b"AI_A", b"CNDT")
PLAYER_MADE = (b"SPEL", b"ENCH", b"ALCH", b"ARMO", b"WEAP", b"CLOT", b"BOOK", b"CLAS", b"MISC")


def zstr(data):
    return data.split(b"\0", 1)[0].decode("latin-1")


def blob(data):
    """An opaque value: compared whole, shown as the byte ranges that differ."""
    return ("blob", bytes(data))


def save_units(path):
    """Every comparable piece of a save, as {(row, label): value}."""
    units = {}
    dial = None
    clones = collections.defaultdict(list)

    def put(row, label, value):
        key = (row, label)
        if key in units:  # a repeated id: keep both
            n = 2
            while (row, f"{label} #{n}") in units:
                n += 1
            key = (row, f"{label} #{n}")
        units[key] = value

    for tag, _flags, data in records(path):
        subs = list(subrecords(data))
        first = dict(reversed(subs))  # first occurrence of each subrecord
        name = zstr(first.get(b"NAME", b""))
        t = tag.decode("latin-1")
        if tag == b"TES3":
            put("Clock and game", "masters", tuple(zstr(v) for s, v in subs if s == b"MAST"))
        elif tag == b"GAME":
            put("Clock and game", "GAME", blob(first.get(b"GMDT", b"")))
        elif tag == b"NPC_" and name.lower() == "player":
            player_npc(subs, put)
        elif tag == b"NPCC" and name.lower() == "playersavegame":
            player_inventory(subs, put)
        elif tag == b"PCDT":
            player_data(subs, put)
        elif tag == b"REFR":
            for s, v in subs:
                if s == b"CHRD" and len(v) == 8 * len(SKILLS):
                    values = struct.unpack(f"<{2 * len(SKILLS)}i", v)
                    for i, k in enumerate(SKILLS):  # base, current
                        put("Level and skills", k, values[2 * i:2 * i + 2])
                    continue
                row = ("Position, cell, heading" if s in (b"DATA", b"STPR")
                       else "Player mobile (attributes, current stats, undecoded)")
                if s not in (b"FRMR", b"NAME", b"ND3D"):
                    put(row, f"REFR {s.decode('latin-1')}", blob(v))
        elif tag == b"SPLM":
            effects = collections.Counter()
            for s, v in subs:
                if s == b"SPDT":
                    spell = zstr(v[4:36])
                elif s == b"NPDT":
                    effects[(spell, zstr(v[:32]))] += 1
            for (spell, caster), n in effects.items():
                put("Active effects", f"{spell} from {caster}", n)
        elif tag == b"GLOB":
            row = "Player globals (PC*)" if name.lower().startswith("pc") else "Globals"
            put(row, name, blob(first.get(b"FLTV", b"")))
        elif tag == b"SCPT":
            head = first.get(b"SCHD", b"")
            put("Global scripts", zstr(head[:32]),
                blob(b"".join(v for s, v in subs if s != b"SCHD")))
        elif tag == b"DIAL":
            dial = name
            put("Journal", name, blob(first.get(b"XIDX", b"")))
        elif tag == b"INFO":
            put("Journal", f"{dial} INFO", blob(data))
        elif tag == b"JOUR":
            put("Journal", "journal text", blob(data))
        elif tag == b"REGN":
            put("Weather", name, blob(data))
        elif tag == b"FMAP":
            put("Map exploration", "world map", blob(data))
        elif tag in (b"KLST", b"STLN"):
            put("Kills, stolen items", f"{t} {name}", blob(data))
        elif tag in (b"CREC", b"NPCC"):  # INDX numbers clones per session: group them by id
            clones[(t, name)].append(b"".join(s + v for s, v in subs if s != b"INDX"))
        elif tag == b"CNTC":
            index = struct.unpack("<I", first[b"INDX"])[0] if b"INDX" in first else 0
            put("Containers", f"{name} {index}", blob(data))
        elif tag == b"CELL":
            cell_units(subs, put)
        elif tag in PLAYER_MADE and name[:1].isdigit():  # ids the game makes up
            put("Player-made records", f"{t} {name}", blob(data))
        elif tag in PLAYER_MADE or tag in (b"NPC_", b"CREA", b"CONT", b"LIGH", b"DOOR"):
            put("Base records changed", f"{t} {name}", blob(data))
        else:
            put("Other", f"{t} {name}", blob(data))
    for (t, name), bodies in clones.items():
        put("Actors (changed)", f"{t} {name}", blob(b"".join(sorted(bodies))))
    return units


def player_npc(subs, put):
    items = collections.defaultdict(list)
    item = None
    for s, v in subs:
        if s in (b"FNAM", b"RNAM", b"CNAM", b"ANAM", b"BNAM", b"KNAM", b"FLAG"):
            put("Identity", f"NPC_ {s.decode('latin-1')}",
                zstr(v) if s != b"FLAG" else struct.unpack("<I", v)[0])
        elif s == b"NPDT" and len(v) == NPDT.size:
            # The base record keeps its chargen stats; only the level follows play. The live
            # skills are the REFR's CHRD.
            f = NPDT.unpack(v)
            put("Level and skills", "level", f[0])
            put("Misc player data", "base record attributes, skills, health/magicka/fatigue",
                f[1:39])
            put("Factions", "reputation", f[40])
            put("Misc player data", "NPDT disposition, rank", (f[39], f[41]))
            put("Inventory and gold", "NPDT gold", f[42])
        elif s == b"NPDT":
            put("Level and skills", "NPDT", blob(v))
        elif s == b"NPCO":
            item = zstr(v[4:36]).lower()
            items[item].append([struct.unpack("<i", v[:4])[0]])
        elif s == b"NPCS":
            put("Known spells", zstr(v).lower(), True)
        elif item is not None and s not in (b"NAME", b"SCRI", b"AIDT") and not s.startswith(b"AI"):
            items[item][-1].append((s.decode("latin-1"), v.hex()))  # item data of the last NPCO
        elif s != b"NAME":
            put("Misc player data", f"NPC_ {s.decode('latin-1')}", blob(v))
    for item, stacks in items.items():
        put("Misc player data", f"base record item {item}", tuple(sorted(tuple(st) for st in stacks)))


def player_inventory(subs, put):
    """The player's NPCC: each NPCO and its item data, then WIDX (NPCO index, item-data index)
    for each equipped stack."""
    stacks, items = [], collections.defaultdict(list)
    for s, v in subs:
        if s == b"NPCO":
            stacks.append((zstr(v[4:36]).lower(), [struct.unpack("<i", v[:4])[0]]))
            items[stacks[-1][0]].append(stacks[-1][1])
        elif s == b"WIDX":
            index, data = struct.unpack("<ii", v)
            item = stacks[index][0] if 0 <= index < len(stacks) else f"#{index}"
            put("Equipped items", item, data)
        elif stacks and s in (b"XIDX", b"XHLT", b"XCHG", b"XSOL", b"SCRI"):
            stacks[-1][1].append((s.decode("latin-1"), v.hex()))
        elif s != b"NAME":
            put("Misc player data", f"NPCC {s.decode('latin-1')}", blob(v))
    for item, entries in items.items():
        put("Inventory and gold", item, tuple(sorted(tuple(e) for e in entries)))


def player_data(subs, put):
    for s, v in subs:
        t = s.decode("latin-1")
        if s == b"DNAM":
            put("Topics", zstr(v).lower(), True)
        elif s == b"FNAM" and len(v) >= FACTION.size:
            rank, rep, flags, faction = FACTION.unpack_from(v)
            put("Factions", zstr(faction), (rank, rep, flags))
        elif s == b"BNAM":
            put("Identity", "birthsign", zstr(v))
        elif s in (b"PNAM", b"SNAM"):
            put("Player data (PNAM, SNAM, undecoded)", t, blob(v))
        elif s == b"ENAM":
            put("Position, cell, heading", "last exterior", struct.unpack_from("<ii", v))
        elif s == b"MNAM":
            put("Misc player data", "mark cell", zstr(v))
        else:
            put("Misc player data", f"PCDT {t}", blob(v))


def cell_units(subs, put):
    """A cell's own fields, then each reference by FRMR, split into position, script and actor
    state."""
    cell, ref, fields = None, None, collections.defaultdict(list)
    made = collections.defaultdict(list)

    def flush():
        for row, parts in fields.items():
            if ref and ref.startswith("made in play"):
                made[(row, f"{cell} {ref}")].append(b"".join(parts))
            else:
                put(row, f"{cell} {ref or '(cell)'}", blob(b"".join(parts)))
        fields.clear()

    def label(i, frmr):
        """Plugin references by FRMR; those made in play (mod index 0) are numbered afresh by
        each session, so by base object instead."""
        if frmr >> 24:
            return f"{frmr:#010x}"
        base = zstr(subs[i + 1][1]) if i + 1 < len(subs) and subs[i + 1][0] == b"NAME" else ""
        if len(base) > 8 and base[-8:].isdigit():  # a clone's session number
            base = base[:-8]
        return f"made in play: {base}"

    for i, (s, v) in enumerate(subs):
        if cell is None and s == b"DATA":
            cell = zstr(subs[0][1]) if subs and subs[0][0] == b"NAME" and subs[0][1] != b"\0" \
                else "({}, {})".format(*struct.unpack_from("<ii", v, 4))
        elif s == b"FRMR":
            flush()
            ref = label(i, struct.unpack("<I", v)[0])
        elif ref is None:
            if cell is not None:
                fields["Map exploration"].append(s + v)
        elif s in SCRIPT_SUBS:
            fields["Script locals"].append(s + v)
        elif s in ACTOR_SUBS:
            fields["Actors (changed)"].append(s + v)
        else:
            fields["Object state"].append(s + v)
    flush()
    for (row, label), parts in made.items():
        put(row, label, blob(b"".join(sorted(parts))))


def show(value):
    if isinstance(value, tuple) and len(value) == 2 and value[0] == "blob":
        return f"{len(value[1])} bytes"
    return repr(value)


def blob_change(a, b):
    """Where two opaque values differ: byte ranges, or the sizes."""
    if len(a) != len(b):
        return f"{len(a)} -> {len(b)} bytes"
    spans, start = [], None
    for i in range(len(a) + 1):
        same = i == len(a) or a[i] == b[i]
        if not same and start is None:
            start = i
        elif same and start is not None:
            spans.append((start, i))
            start = None
    text = ", ".join(f"+{s:#x}" + (f"..{e - 1:#x}" if e - s > 1 else "") for s, e in spans[:6])
    return f"differs at {text}" + (f" (+{len(spans) - 6} more)" if len(spans) > 6 else "")


def diff(reference, rebuilt):
    """{row: [line]} of what a rebuilt save lacks, adds or changes against the reference."""
    a, b = save_units(reference), save_units(rebuilt)
    out = {row: [] for row in ROWS}
    for key in sorted(set(a) | set(b)):
        row, label = key
        if key not in b:
            out[row].append(f"missing   {label} = {show(a[key])}")
        elif key not in a:
            out[row].append(f"extra     {label} = {show(b[key])}")
        elif a[key] != b[key]:
            va, vb = a[key], b[key]
            if isinstance(va, tuple) and va[:1] == ("blob",) and vb[:1] == ("blob",):
                out[row].append(f"changed   {label}: {blob_change(va[1], vb[1])}")
            else:
                out[row].append(f"changed   {label}: {va!r} -> {vb!r}")
    return out, a, b


def report_diff(reference, rebuilt, limit):
    out, a, _b = diff(reference, rebuilt)
    print(f"  reference {reference}\n  rebuilt   {rebuilt}\n")
    width = max(map(len, ROWS))
    print(f"  {'row':<{width}} {'units':>6} {'differ':>7}")
    held = collections.Counter(row for row, _ in a)
    for row in ROWS:
        mark = "" if out[row] else "  same"
        print(f"  {row:<{width}} {held[row]:>6} {len(out[row]):>7}{mark}")
    for row in ROWS:
        if out[row]:
            print(f"\n  {row}")
            for line in out[row][:limit]:
                print(f"    {line}")
            if len(out[row]) > limit:
                print(f"    ... {len(out[row]) - limit} more")
    return sum(len(v) for v in out.values())


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("saves", nargs="+")
    ap.add_argument("--detail", action="store_true", help="per-record-type breakdown")
    ap.add_argument("--bindings", action="store_true",
                    help="MAST list and what changed refs are bound to")
    ap.add_argument("--poison-first-per-cell", metavar="OUT",
                    help="copy one save, changing its first index-1 CELL reference to index 255")
    ap.add_argument("--poison-first-in-cell", nargs=2, metavar=("CELL", "OUT"),
                    help="copy one save, poisoning one reference in the named CELL")
    ap.add_argument("--diff", action="store_true",
                    help="compare two saves (a reference, then its rebuild) by coverage row")
    ap.add_argument("--limit", type=int, default=20, help="lines shown per row with --diff")
    a = ap.parse_args()

    if a.diff:
        if len(a.saves) != 2:
            raise SystemExit("--diff takes a reference save and a rebuilt one")
        return report_diff(a.saves[0], a.saves[1], a.limit)

    if a.poison_first_per_cell or a.poison_first_in_cell:
        if len(a.saves) != 1:
            raise SystemExit("poisoning requires exactly one input save")
        try:
            if a.poison_first_in_cell:
                cell_name, out = a.poison_first_in_cell
                cells, patched = poison_first_in_cell(a.saves[0], out, cell_name)
            else:
                out = a.poison_first_per_cell
                cells, patched = poison_first_per_cell(a.saves[0], out)
        except ValueError as exc:
            raise SystemExit(str(exc))
        print(f"  {out}: {patched}/{cells} CELL records poisoned")
        return

    if a.bindings:
        return report_bindings(sorted(a.saves))

    rows = []
    for path in sorted(a.saves):
        try:
            rows.append(analyse(path))
        except ValueError as exc:
            print(f"  {os.path.basename(path)}: {exc}")

    print(f"  {'save':<28} {'bytes':>9} {'recs':>6} {'FRMR':>6} {'NPCO':>6} "
          f"{'cellKB':>7} {'npccKB':>7} {'ok':>5}")
    for r in rows:
        name = os.path.basename(r["path"])[:28]
        print(f"  {name:<28} {r['size']:>9} {r['records']:>6} {r['frmr']:>6} {r['npco']:>6} "
              f"{r['bytes_by_tag'][b'CELL'] // 1024:>7} {r['bytes_by_tag'][b'NPCC'] // 1024:>7} "
              f"{100 * r['accounted'] // r['size']:>4}%")

    if a.detail and rows:
        for r in rows:
            print(f"\n  {os.path.basename(r['path'])}")
            for tag, nbytes in r["bytes_by_tag"].most_common(12):
                print(f"    {tag.decode('latin-1'):<6} {r['by_tag'][tag]:>6} recs "
                      f"{nbytes:>9} bytes  {100 * nbytes / r['size']:>5.1f}%")


if __name__ == "__main__":
    main()
