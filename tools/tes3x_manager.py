#!/usr/bin/env python3
"""Install the TES3X console manager on an Xbox.

`stage OUT` writes the manager's folder as a deploy tree (OUT/deploy) with its manifest, and
OUT/console.ini naming the manager for the game; `install` stages and deploys it, over FTP or
through a running manager's agent. The XBE comes from --xbe, the copy a package ships
(manager/default.xbe), or an nxdk build of manager/ (paths.nxdk).
"""

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import tes3x_deploy
import tes3x_ftp
import tes3x_manifest
import tes3x_nxdk
import tes3x_targets
from tes3x_paths import xbox_root
from tes3x_pipeline import dashboard_xml
from tes3x_xbe import Xbe

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "manager"
PACKAGED = SOURCE / "default.xbe"
FOLDER = "TES3XManager"
TITLE = "TES3X Manager"
PROFILE = "manager"
# The manager does not list a folder of this layout among the builds.
LAYOUT = "manager"


class ManagerError(Exception):
    pass


def version():
    match = re.search(r'#define MGR_VERSION "([^"]+)"',
                      (SOURCE / "mgr.h").read_text(encoding="utf-8"))
    return match.group(1) if match else None


def find_xbe(given=None, config=None, work=None):
    if given:
        if not Path(given).is_file():
            raise ManagerError(f"not found: {given}")
        return Path(given)
    if PACKAGED.is_file():
        return PACKAGED
    out = Path(work or Path.cwd()) / "build" / "manager" / "app"
    print(f"building the manager in {out}", flush=True)
    try:
        return tes3x_nxdk.build(SOURCE, out, config=config)
    except tes3x_nxdk.NxdkError as exc:
        raise ManagerError(f"no manager XBE: {exc}") from exc


def remote_folder(target, folder=FOLDER):
    games = target.get("games_root")
    if not games:
        raise ManagerError(f"target {target['name']!r} needs games_root")
    return xbox_root(games).rstrip("/") + "/" + folder


def stage(out, xbe, remote):
    """Write the manager's deploy tree and console.ini under `out`; returns the tree."""
    out = Path(out)
    tree = out / "deploy"
    if tree.exists():
        shutil.rmtree(tree)
    (tree / "_resources").mkdir(parents=True)
    shutil.copyfile(xbe, tree / "default.xbe")
    title_id = Xbe(Path(xbe).read_bytes()).cert()["title_id"]
    folder = remote.rsplit("/", 1)[-1]
    (tree / "_resources" / "default.xml").write_text(dashboard_xml(TITLE, folder, title_id),
                                                     encoding="utf-8")
    manifest = tes3x_manifest.create(
        tree, profile=PROFILE, source={"kind": "manager", "version": version()},
        install_layout=LAYOUT)
    # Not rebuilt from a retail image, whatever its suffix says.
    manifest["files"]["default.xbe"]["origin"] = "build"
    tes3x_manifest.write(tree, manifest)
    # Deploy takes the folder's owner from this; a manager folder belongs to no other profile.
    (out / tes3x_deploy.PIPELINE_MARKER).write_text(
        json.dumps({"profile": PROFILE, "install_layout": LAYOUT, "remote": remote,
                    "version": version()}) + "\n", encoding="utf-8")
    path = remote.replace("/", "\\") + "\\default.xbe"
    (out / "console.ini").write_text(f"[Xbox]\r\nManager={path}\r\n", encoding="latin-1")
    return tree


def deploy_arguments(out, remote, args):
    arguments = [str(Path(out) / "deploy"), "--remote", remote, "--verify", "size",
                 "--console-ini", str(Path(out) / "console.ini")]
    for name in ("config", "target"):
        if getattr(args, name):
            arguments += [f"--{name}", getattr(args, name)]
    for flag in ("dry_run", "replace", "agent"):
        if getattr(args, flag):
            arguments.append("--" + flag.replace("_", "-"))
    return arguments


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="command", required=True)
    for name in ("stage", "install"):
        p = sub.add_parser(name)
        if name == "stage":
            p.add_argument("out", help="folder for deploy/ and console.ini")
        else:
            p.add_argument("--out", help="staging folder (default: build/manager/install)")
            p.add_argument("--dry-run", action="store_true")
            p.add_argument("--replace", action="store_true",
                           help="install over a folder that holds something else")
            p.add_argument("--agent", action="store_true",
                           help="send through the running manager's agent instead of FTP")
        p.add_argument("--xbe", help="the manager XBE (default: packaged, else built)")
        p.add_argument("--folder", default=FOLDER, help=f"folder under games_root ({FOLDER})")
        p.add_argument("--config", help="local config (default: ./tes3x.local.toml)")
        p.add_argument("--target", help="Xbox target (default: default_target)")
    args = ap.parse_args()

    local = tes3x_ftp.local_settings(args.config)
    try:
        target = tes3x_targets.resolve(local, args.target, "xbox", required=True)
        remote = remote_folder(target, args.folder)
        xbe = find_xbe(args.xbe, args.config)
    except (tes3x_targets.TargetError, ManagerError) as exc:
        sys.exit(str(exc))
    out = Path(getattr(args, "out", None) or Path.cwd() / "build" / "manager" / "install")
    stage(out, xbe, remote)
    print(f"manager {version()} staged for {remote}", flush=True)
    if args.command == "install":
        args.target = target["name"]
        sys.exit(subprocess.call([sys.executable, str(Path(__file__).with_name("tes3x_deploy.py")),
                                  *deploy_arguments(out, remote, args)]))


if __name__ == "__main__":
    main()
