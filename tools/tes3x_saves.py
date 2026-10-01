#!/usr/bin/env python3
"""List and move Morrowind saves between the Xbox, an xemu disk and a PC save library.

    python tools/tes3x_saves.py list --pool 42530005 --xbox --disk build/play/p/hdd.qcow2 \\
        --library build/saves
    python tools/tes3x_saves.py pull --pool 42530005 --from xbox 1B22410A51D3 --library build/saves
    python tools/tes3x_saves.py push --pool 5433A1F2 --pool-name "TR test" 1B22410A51D3 \\
        --library build/saves
    python tools/tes3x_saves.py copy --pool 42530005 --to 5433A1F2 --to-name "TR test" \\
        --where xbox --move 1B22410A51D3 --library build/saves
    python tools/tes3x_saves.py delete --pool 42530005 1B22410A51D3

A save is its folder under E:/UDATA/<pool>: <name>.ess, SaveMeta.xbx, saveimage.xbx and vv.dat.
It is moved whole; the game finds it by the name in SaveMeta.xbx and checks vv.dat. A folder
without SaveMeta.xbx, such as TES3X with its loose test saves, is not listed. The library
keeps copies as <library>/<pool>/<folder>/, and saves.json there the last Xbox listing of each
pool and the names of known pools. `list` prints JSON; the rest print progress and exit 3 when a
push or copy would replace a save, unless --replace.
"""

import argparse
import contextlib
import datetime
import ftplib
import io
import json
import os
from pathlib import Path
import posixpath
import shutil
import struct
import sys
import tempfile
import tomllib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tes3x_deploy import CONFLICT, ensure_dirs, ftp_basename, pool_plan, write_pool  # noqa: E402
from tes3x_fatx import ATTR_DIRECTORY, DIRENT, END_OF_DIR, PARTITIONS, FatxReader  # noqa: E402
from tes3x_put import make_dirs, put_file  # noqa: E402
from tes3x_qcow2 import CowView, create_overlay, open_image  # noqa: E402
import tes3x_ftp  # noqa: E402
import tes3x_savepool  # noqa: E402

META = "SaveMeta.xbx"
DELETED = 0xE5
# The TES3 header record: HEDR, the MAST list, GMDT and a 16 KB screenshot. Nothing past it.
HEAD_LIMIT = 1 << 17


def parse_header(data):
    """Save name, player, cell and masters from the start of a .ess file."""
    out = {"title": None, "player": None, "cell": None, "masters": []}
    if data[:4] != b"TES3" or len(data) < 16:
        return out
    end = min(len(data), 16 + struct.unpack_from("<I", data, 4)[0])
    i = 16
    while i + 8 <= end:
        tag, size = data[i:i + 4], struct.unpack_from("<I", data, i + 4)[0]
        body = data[i + 8:i + 8 + size]
        if tag == b"HEDR" and len(body) >= 296:
            out["title"] = cstr(body[40:296])
        elif tag == b"MAST":
            out["masters"].append(cstr(body))
        elif tag == b"GMDT" and len(body) >= 124:
            out["cell"] = cstr(body[24:88])
            out["player"] = cstr(body[92:124])
        i += 8 + size
    return out


def cstr(raw):
    return raw.split(b"\0", 1)[0].decode("latin-1")


def meta_name(raw):
    text = raw.decode("utf-16", "replace") if raw[:2] in (b"\xff\xfe", b"\xfe\xff") \
        else raw.decode("latin-1")
    for line in text.splitlines():
        key, _, value = line.partition("=")
        if key.strip().lower() == "name":
            return value.strip()
    return None


def entry(source, folder, files, head, meta, date):
    ess = next((name for name in files if name.lower().endswith(".ess")), None)
    info = parse_header(head or b"")
    return {"source": source, "folder": folder, "name": meta_name(meta) if meta else None,
            "ess": ess, "size": sum(files.values()), "date": date, **info}


# Xbox

def ftp_list(ftp, path):
    """(name, size, is_dir, date) of a directory's entries; [] when it is missing."""
    try:
        ftp.cwd(path)
    except ftplib.all_errors:
        return []
    lines = []
    ftp.retrlines("LIST", lines.append)
    out = []
    for line in lines:
        parts = line.split(maxsplit=8)
        if len(parts) < 9 or parts[8] in (".", ".."):
            continue
        try:
            size = int(parts[4])
        except ValueError:
            size = -1
        out.append((parts[8], size, line[0] == "d", list_date(parts[5:8])))
    return out


def list_date(fields, now=None):
    """A LIST date as YYYY-MM-DD HH:MM. Like ls, a recent file shows a time and no year."""
    now = now or datetime.datetime.now()
    month, day, last = fields
    try:
        if ":" in last:
            stamp = datetime.datetime.strptime(f"{month} {day} {now.year} {last}", "%b %d %Y %H:%M")
            if stamp > now + datetime.timedelta(days=1):
                stamp = stamp.replace(year=now.year - 1)
        else:
            stamp = datetime.datetime.strptime(f"{month} {day} {last}", "%b %d %Y")
    except ValueError:
        return " ".join(fields)
    return stamp.strftime("%Y-%m-%d %H:%M")


def read_head(ftp, path):
    """The first bytes of a remote file, up to the end of its header record."""
    conn = ftp.transfercmd(f"RETR {ftp_basename(ftp, path)}")
    data = b""
    need = HEAD_LIMIT
    try:
        while len(data) < need:
            chunk = conn.recv(65536)
            if not chunk:
                break
            data += chunk
            if len(data) >= 8 and data[:4] == b"TES3":
                need = min(HEAD_LIMIT, 16 + struct.unpack_from("<I", data, 4)[0])
    finally:
        conn.close()
    try:
        ftp.voidresp()
    except ftplib.all_errors:
        pass
    return data[:need]


def read_all(ftp, path):
    buf = io.BytesIO()
    ftp.retrbinary(f"RETR {ftp_basename(ftp, path)}", buf.write)
    return buf.getvalue()


class Xbox:
    """FTP to the console, reconnecting when an abandoned transfer leaves the session unusable."""

    def __init__(self, args):
        self.args = args
        self.ftp = tes3x_ftp.connect(args)

    def fresh(self):
        try:
            self.ftp.voidcmd("NOOP")
        except ftplib.all_errors:
            try:
                self.ftp.close()
            except ftplib.all_errors:
                pass
            self.ftp = tes3x_ftp.connect(self.args)
        return self.ftp

    def saves(self, pool, cache):
        base = "E:/" + tes3x_savepool.folder(pool)
        found = []
        for name, _size, is_dir, date in ftp_list(self.fresh(), base):
            if not is_dir:
                continue
            listing = ftp_list(self.fresh(), f"{base}/{name}")
            files = {f: s for f, s, d, _ in listing if not d}
            meta = next((f for f in files if f.lower() == META.lower()), None)
            if meta is None:
                continue
            ess = next((f for f in files if f.lower().endswith(".ess")), None)
            key = f"{pool:08X}/{name}/{ess}/{files.get(ess)}"
            if ess and key not in cache:
                cache[key] = read_head(self.fresh(), f"{base}/{name}/{ess}").hex()
            meta_raw = read_all(self.fresh(), f"{base}/{name}/{meta}")
            head = bytes.fromhex(cache[key]) if ess else None
            found.append(entry("xbox", name, files, head, meta_raw, date))
        return found

    def pools(self):
        """Save pools on the console: every E:/UDATA folder that carries tes3xpool.txt."""
        out = []
        for name, _size, is_dir, _date in ftp_list(self.fresh(), "E:/UDATA"):
            if not is_dir or not name.upper().startswith(f"{tes3x_savepool.PREFIX:04X}"):
                continue
            try:
                label = read_all(self.fresh(), f"E:/UDATA/{name}/{tes3x_savepool.MARKER}")
            except ftplib.all_errors:
                continue
            out.append({"id": name.upper(), "name": label.decode("utf-8", "replace").strip()})
        return out

    def pull(self, pool, folder, out):
        base = f"E:/{tes3x_savepool.folder(pool)}/{folder}"
        listing = [e for e in ftp_list(self.fresh(), base) if not e[2]]
        if not listing:
            raise SystemExit(f"{base}: no such save")
        out.mkdir(parents=True, exist_ok=True)
        for name, _size, _dir, _date in listing:
            (out / name).write_bytes(read_all(self.fresh(), f"{base}/{name}"))
        return len(listing)


# xemu disk

def fat_date(value):
    date, tm = value >> 16, value & 0xFFFF
    try:
        return datetime.datetime(2000 + (date >> 9), (date >> 5) & 15, date & 31,
                                 tm >> 11, (tm >> 5) & 63, (tm & 31) * 2).strftime(
            "%Y-%m-%d %H:%M")
    except ValueError:
        return None


class Disk:
    def __init__(self, image):
        self.image = image

    def entries(self, fs, cluster):
        """(name, attrs, first, size, date) with the entry's latest FATX timestamp."""
        blob = fs.read_chain(cluster)
        out = []
        for at in range(0, len(blob), DIRENT):
            raw = blob[at:at + DIRENT]
            if not raw or raw[0] in (END_OF_DIR, 0x00):
                break
            if raw[0] == 0xE5:
                continue
            first, size = struct.unpack_from("<II", raw, 0x2C)
            stamps = struct.unpack_from("<III", raw, 0x34)
            out.append((raw[2:2 + raw[0]].decode("latin-1"), raw[1], first, size,
                        fat_date(max(stamps))))
        return out

    def folder(self, fs, path):
        cluster = 1
        for part in path.split("/"):
            hit = [e for e in self.entries(fs, cluster)
                   if e[0].lower() == part.lower() and e[1] & ATTR_DIRECTORY]
            if not hit:
                return None
            cluster = hit[0][2]
        return cluster

    def saves(self, pool):
        off, size = PARTITIONS["E"]
        found = []
        with open_image(str(self.image)) as img:
            fs = FatxReader(img, off).bind(size)
            base = self.folder(fs, tes3x_savepool.folder(pool))
            if base is None:
                return []
            for name, attrs, first, _size, date in self.entries(fs, base):
                if not attrs & ATTR_DIRECTORY:
                    continue
                files = {e[0]: e for e in self.entries(fs, first) if not e[1] & ATTR_DIRECTORY}
                meta = next((f for f in files if f.lower() == META.lower()), None)
                if meta is None:
                    continue
                ess = next((f for f in files if f.lower().endswith(".ess")), None)
                head = fs.read_chain(files[ess][2], min(files[ess][3], HEAD_LIMIT)) \
                    if ess else None
                meta_raw = fs.read_chain(files[meta][2], files[meta][3])
                found.append(entry("xemu", name, {f: e[3] for f, e in files.items()}, head,
                                   meta_raw, max((e[4] or "" for e in files.values()),
                                                 default=date)))
        return found

    def pull(self, pool, folder, out):
        off, size = PARTITIONS["E"]
        with open_image(str(self.image)) as img:
            fs = FatxReader(img, off).bind(size)
            cluster = self.folder(fs, f"{tes3x_savepool.folder(pool)}/{folder}")
            if cluster is None:
                raise SystemExit(f"{self.image}: no save {folder} in pool {pool:08X}")
            files = [e for e in self.entries(fs, cluster) if not e[1] & ATTR_DIRECTORY]
            out.mkdir(parents=True, exist_ok=True)
            for name, _attrs, first, nbytes, _date in files:
                (out / name).write_bytes(fs.read_chain(first, nbytes))
        return len(files)

    def has(self, path, name):
        off, size = PARTITIONS["E"]
        with open_image(str(self.image)) as img:
            fs = FatxReader(img, off).bind(size)
            cluster = self.folder(fs, path)
            return cluster is not None and any(
                e[0].lower() == name.lower() for e in self.entries(fs, cluster))

    @contextlib.contextmanager
    def edit(self):
        """A writable view of the disk whose changes become a new overlay stacked on it, so no
        layer below is touched. xemu must not be running on the disk."""
        image = Path(self.image)
        n = 1
        while image.with_name(f"{image.stem}-{n}{image.suffix}").exists():
            n += 1
        below = image.rename(image.with_name(f"{image.stem}-{n}{image.suffix}"))
        try:
            with CowView(str(below)) as view:
                yield view
                create_overlay(str(image), str(below), view.changed())
        except BaseException:
            if not image.exists():
                below.rename(image)
            raise

    def remove(self, view, pool, folder):
        """Delete a save folder: free its clusters and mark its entries deleted."""
        off, size = PARTITIONS["E"]
        fs = FatxReader(view, off).bind(size)
        parent = self.folder(fs, tes3x_savepool.folder(pool))
        hit = find_entry(fs, parent, folder) if parent is not None else None
        if hit is None:
            raise SystemExit(f"{self.image}: no save {folder} in pool {pool:08X}")
        marks, first = [hit[0]], struct.unpack_from("<I", hit[1], 0x2C)[0]
        freed = fs.chain(first)
        for name, _attrs, child, _size, _date in self.entries(fs, first):
            marks.append(find_entry(fs, first, name)[0])
            if child:
                freed += fs.chain(child)
        fat = bytearray(fs.fat)
        fmt = "<I" if fs.fat32 else "<H"
        for cluster in freed:
            struct.pack_into(fmt, fat, cluster * fs.fat_entry, 0)
        view.seek(fs.fat_offset)
        view.write(bytes(fat))
        for at in marks:
            view.seek(at)
            view.write(bytes([DELETED]))

    def put(self, view, path, source):
        """Write every file of a host folder into `path`, creating it."""
        make_dirs(view, path)
        for item in sorted(Path(source).iterdir()):
            if item.is_file():
                put_file(view, str(item), path, item.name)


def find_entry(fs, cluster, name):
    """(image offset, raw entry) of the live directory entry called name, or None."""
    for c in fs.chain(cluster):
        base = fs.data_offset + (c - 1) * fs.cluster_size
        fs.img.seek(base)
        blob = fs.img.read(fs.cluster_size)
        for at in range(0, len(blob), DIRENT):
            raw = blob[at:at + DIRENT]
            if not raw or raw[0] in (END_OF_DIR, 0x00):
                return None
            if raw[0] != DELETED and raw[2:2 + raw[0]].decode("latin-1").lower() == name.lower():
                return base + at, raw
    return None


def disk_write(args, pool, target, folders, from_library, move):
    """Put saves into pool `target` on the xemu disk, from the PC library or from the disk's
    own pool `pool`, deleting the originals on a move. False on a conflict."""
    disk = Disk(args.disk)
    base = tes3x_savepool.folder(target)
    clashes = [folder for folder in folders if disk.has(base, folder)]
    if clashes and not args.replace:
        for folder in clashes:
            print(f"  conflict: the xemu disk's pool {target:08X} already holds {folder}")
        return False
    with tempfile.TemporaryDirectory() as tmp:
        sources = {}
        for folder in folders:
            if from_library:
                sources[folder] = Path(args.library) / f"{pool:08X}" / folder
                if not sources[folder].is_dir():
                    raise SystemExit(f"no library save {folder}")
            else:
                sources[folder] = Path(tmp) / folder
                disk.pull(pool, folder, sources[folder])
        pool_dir = None
        name = args.to_name or args.pool_name
        if target != tes3x_savepool.SHARED_ID and not disk.has(base, "TitleImage.xbx"):
            if not name:
                raise SystemExit("the pool's name is needed to make its folder on the disk")
            pool_dir = Path(tmp) / "pool-files"
            pool_dir.mkdir()
            image = tes3x_savepool.title_image(retail_launcher(args.config))
            for file_name, data in tes3x_savepool.files(name, image).items():
                (pool_dir / file_name).write_bytes(data)
        with disk.edit() as view:
            for folder in clashes:
                disk.remove(view, target, folder)
            if pool_dir:
                disk.put(view, base, pool_dir)
            for folder, source in sources.items():
                disk.put(view, f"{base}/{folder}", source)
            if move:
                for folder in folders:
                    disk.remove(view, pool, folder)
    print(f"  {'moved' if move else 'copied'} {len(folders)} save(s) to the xemu disk's "
          f"pool {target:08X}")
    return True


# PC library

def library_saves(root, pool):
    found = []
    base = Path(root) / f"{pool:08X}"
    for folder in sorted(p for p in base.iterdir() if p.is_dir()) if base.is_dir() else []:
        files = {p.name: p.stat().st_size for p in folder.iterdir() if p.is_file()}
        meta = next((f for f in files if f.lower() == META.lower()), None)
        if meta is None:
            continue
        ess = next((f for f in files if f.lower().endswith(".ess")), None)
        head = None
        if ess:
            with open(folder / ess, "rb") as f:
                head = f.read(HEAD_LIMIT)
        stamp = max((p.stat().st_mtime for p in folder.iterdir()), default=0)
        found.append(entry("pc", folder.name, files, head,
                           (folder / meta).read_bytes(),
                           datetime.datetime.fromtimestamp(stamp).strftime("%Y-%m-%d %H:%M")))
    return found


def push(xbox, pool, pool_name, source, replace):
    """Copy a library save folder into a pool on the Xbox."""
    base = f"E:/{tes3x_savepool.folder(pool)}"
    ftp = xbox.fresh()
    if pool != tes3x_savepool.SHARED_ID:
        if not pool_name:
            raise SystemExit("--pool-name is needed to push into a save pool")
        record = {"name": pool_name, "id": f"{pool:08X}"}
        pool_base, conflicts, missing = pool_plan(ftp, record)
        if conflicts and not replace:
            for line in conflicts:
                print(f"  conflict: {line}")
            return False
        if missing:
            image = tes3x_savepool.title_image(retail_launcher(xbox.args.config))
            write_pool(xbox.fresh(), pool_base, record, image, missing)
    target = f"{base}/{source.name}"
    if ftp_list(xbox.fresh(), target) and not replace:
        print(f"  conflict: {target} already holds a save with this name")
        return False
    local = {p.name: p.stat().st_size for p in source.iterdir() if p.is_file()}
    for name in sorted(local):
        ftp = xbox.fresh()
        ensure_dirs(ftp, f"{target}/{name}", set())
        with open(source / name, "rb") as f:
            ftp.storbinary(f"STOR {name}", f)
    remote = {name.lower(): size for name, size, is_dir, _ in ftp_list(xbox.fresh(), target)
              if not is_dir}
    wrong = [name for name, size in local.items() if remote.get(name.lower()) != size]
    if wrong:
        raise SystemExit(f"{target}: {', '.join(wrong)} did not arrive whole")
    print(f"  copied {source.name} to {target}")
    return True


def copy(args, pool, target, folder, xbox):
    """Copy or move one save into pool `target`, where it is. False on a conflict."""
    library = Path(args.library)
    here = library / f"{pool:08X}" / folder
    there = library / f"{target:08X}" / folder
    if args.where == "xbox":
        # The PC keeps a copy of anything that leaves the Xbox.
        xbox.pull(pool, folder, here)
        if not push(xbox, target, args.to_name, here, args.replace):
            return False
        if args.move:
            delete(xbox, pool, folder)
            move_folder(here, there)
        return True
    if there.exists() and not args.replace:
        print(f"  conflict: the PC library's pool {target:08X} already holds {folder}")
        return False
    if args.where == "xemu":
        Disk(args.disk).pull(pool, folder, there)
    elif args.move:
        move_folder(here, there)
    else:
        shutil.rmtree(there, ignore_errors=True)
        shutil.copytree(here, there)
    print(f"  {'moved' if args.move else 'copied'} {folder} to PC pool {target:08X}")
    return True


def move_folder(source, target):
    shutil.rmtree(target, ignore_errors=True)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(source), str(target))


# What the tab showed last: the Xbox listing of each pool, and every pool's name.
INDEX = "saves.json"


def read_index(library):
    try:
        index = json.loads((Path(library) / INDEX).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        index = {}
    index.setdefault("pools", {})
    index.setdefault("xbox", {})
    return index


def write_index(library, index):
    Path(library).mkdir(parents=True, exist_ok=True)
    (Path(library) / INDEX).write_text(json.dumps(index, indent=1), encoding="utf-8")


def remember_pool(library, value, name):
    index = read_index(library)
    index["pools"][f"{value:08X}"] = name
    write_index(library, index)


def retail_launcher(config=None):
    """The retail Default.xbe, whose title image a new pool folder gets."""
    config = Path(config or os.environ.get("TES3X_CONFIG") or Path.cwd() / "tes3x.local.toml")
    with open(config, "rb") as f:
        root = tomllib.load(f).get("paths", {}).get("vanilla_root")
    if not root:
        raise SystemExit("set [paths] vanilla_root in tes3x.local.toml")
    root = Path(root)
    return (root if root.is_absolute() else config.parent / root) / "Default.xbe"


def delete(xbox, pool, folder):
    base = f"E:/{tes3x_savepool.folder(pool)}/{folder}"
    listing = ftp_list(xbox.fresh(), base)
    if not listing:
        raise SystemExit(f"{base}: no such save")
    for name, _size, is_dir, _date in listing:
        if not is_dir:
            ftp = xbox.fresh()
            ftp.delete(ftp_basename(ftp, f"{base}/{name}"))
    ftp = xbox.fresh()
    ftp.cwd(posixpath.dirname(base))
    ftp.rmd(folder)
    print(f"  deleted {base}")


def list_xbox(args, pool):
    """The pool's Xbox saves, stored in the library index; the stored ones when offline."""
    library = Path(args.library or ".")
    heads_path = library / ".headers.json"
    try:
        heads = json.loads(heads_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        heads = {}
    index = read_index(library)
    key = f"{pool:08X}"
    try:
        tes3x_ftp.resolve(args)
        xbox = Xbox(args)
        saves = xbox.saves(pool, heads)
        pools = xbox.pools()
        xbox.ftp.quit()
    except (*ftplib.all_errors, SystemExit) as exc:
        cached = index["xbox"].get(key, {})
        return {"saves": cached.get("saves", []), "pools": [], "time": cached.get("time"),
                "xbox": f"offline: {exc}"}
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    index["xbox"][key] = {"time": now, "saves": saves}
    for found in pools:
        index["pools"].setdefault(found["id"], found["name"])
    if args.library:
        write_index(library, index)
        heads_path.write_text(json.dumps(heads), encoding="utf-8")
    return {"saves": saves, "pools": pools, "time": now, "xbox": "ok"}


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=("list", "pull", "push", "copy", "delete"))
    ap.add_argument("folders", nargs="*", help="save folders to pull, push, copy or delete")
    ap.add_argument("--pool", required=True, help="title ID of the save pool, 8 hex digits")
    ap.add_argument("--pool-name", help="push: the pool's name, for a pool folder not yet made")
    ap.add_argument("--xbox", action="store_true",
                    help="list: the Xbox, saving the result in the library's index; offline, "
                         "the last listing is printed instead")
    ap.add_argument("--disk", help="list: include this xemu disk; pull --from xemu reads it")
    ap.add_argument("--library", help="the PC save library")
    ap.add_argument("--from", dest="source", choices=("xbox", "xemu"), default="xbox",
                    help="pull: where the saves are")
    ap.add_argument("--to", help="copy: the pool to copy into")
    ap.add_argument("--to-name", help="copy: that pool's name, for a pool folder not yet made")
    ap.add_argument("--where", choices=("xbox", "pc", "xemu"), default="xbox",
                    help="copy, delete: where the saves are (copy keeps them there, in the "
                         "other pool); push: xbox or xemu, where the PC copies go")
    ap.add_argument("--move", action="store_true",
                    help="copy: delete each original once its copy is complete")
    ap.add_argument("--replace", action="store_true",
                    help="push, copy: replace a save of the same name, or use a pool folder "
                         "that is not this pool's")
    tes3x_ftp.add_arguments(ap)
    args = ap.parse_args()
    pool = int(args.pool, 16)
    sys.stdout.reconfigure(errors="replace")

    if args.command == "list":
        result = {"saves": [], "pools": [], "xbox": None, "time": None}
        if args.library:
            result["saves"] += library_saves(args.library, pool)
        if args.disk and Path(args.disk).is_file():
            result["saves"] += Disk(args.disk).saves(pool)
        if args.xbox:
            result.update(list_xbox(args, pool))
        print(json.dumps(result))
        return

    if not args.folders:
        ap.error(f"{args.command} needs save folders")
    if args.command == "copy":
        if not args.library or not args.to:
            ap.error("copy needs --library and --to")
        if args.where == "xemu" and not args.disk:
            ap.error("--where xemu needs --disk")
        target = int(args.to, 16)
        if target != tes3x_savepool.SHARED_ID and args.to_name:
            remember_pool(args.library, target, args.to_name)
        if args.where == "xemu":
            if not disk_write(args, pool, target, args.folders, False, args.move):
                print("nothing replaced; copy with --replace to go ahead")
                sys.exit(CONFLICT)
            return
        xbox = None
        if args.where == "xbox":
            tes3x_ftp.resolve(args)
            xbox = Xbox(args)
        ok = all([copy(args, pool, target, folder, xbox) for folder in args.folders])
        if not ok:
            print("nothing replaced; copy with --replace to go ahead")
            sys.exit(CONFLICT)
        return
    if args.command == "pull":
        if not args.library:
            ap.error("pull needs --library")
        source = Disk(args.disk) if args.source == "xemu" else None
        if source is None:
            tes3x_ftp.resolve(args)
            source = Xbox(args)
        for folder in args.folders:
            out = Path(args.library) / f"{pool:08X}" / folder
            n = source.pull(pool, folder, out)
            print(f"  pulled {folder}: {n} files -> {out}")
        return

    if args.command == "delete" and args.where != "xbox":
        if args.where == "xemu":
            disk = Disk(args.disk)
            with disk.edit() as view:
                for folder in args.folders:
                    disk.remove(view, pool, folder)
        else:
            for folder in args.folders:
                shutil.rmtree(Path(args.library) / f"{pool:08X}" / folder)
        print(f"  deleted {len(args.folders)} save(s)")
        return
    if args.command == "push" and args.where == "xemu":
        if not args.library or not args.disk:
            ap.error("push --where xemu needs --library and --disk")
        if not disk_write(args, pool, pool, args.folders, True, False):
            print("nothing replaced; push with --replace to go ahead")
            sys.exit(CONFLICT)
        return

    tes3x_ftp.resolve(args)
    xbox = Xbox(args)
    if args.command == "delete":
        for folder in args.folders:
            delete(xbox, pool, folder)
        return
    if not args.library:
        ap.error("push needs --library")
    ok = True
    for folder in args.folders:
        source = Path(folder)
        if not source.is_dir():
            source = Path(args.library) / f"{pool:08X}" / folder
        if not source.is_dir():
            raise SystemExit(f"no library save {folder}")
        ok &= push(xbox, pool, args.pool_name, source, args.replace)
    if not ok:
        print("nothing replaced; push with --replace to go ahead")
        sys.exit(CONFLICT)


if __name__ == "__main__":
    try:
        main()
    except (OSError, EOFError, ftplib.Error) as exc:
        raise SystemExit(f"the Xbox did not answer: {exc}")
