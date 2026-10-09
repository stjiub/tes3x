#!/usr/bin/env python3
"""Install and update the TES3X console manager on an Xbox.

`stage OUT` writes the manager's folder as a deploy tree (OUT/deploy) with its manifest, and
OUT/console.ini naming the manager for the game and this PC as its agent (NetAgent); `install` stages and deploys it, over FTP or
through a running manager's agent. The folder holds the launcher as default.xbe and the manager
in slot a. The XBEs come from --xbe and --launcher, the copies a package ships
(manager/default.xbe, manager/launcher.xbe), or nxdk builds of manager/ (paths.nxdk).

`update RELEASE` sends a signed release (tes3x release manager) to E:/TES3X/update; through
the agent it then restarts the manager, which installs the release into its other slot.
"""

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import tes3x.deploy as tes3x_deploy
import tes3x.ftp as tes3x_ftp
import tes3x.manifest as tes3x_manifest
import tes3x.agent as tes3x_agent
import tes3x.nxdk as tes3x_nxdk
import tes3x.release as tes3x_release
import tes3x.targets as tes3x_targets
from tes3x.paths import CONFIG_HELP, bundled, checkout, local_config, resource, xbox_root
from tes3x.pipeline import PipelineError, agent_setting, dashboard_xml
from tes3x.xbe import Xbe

SOURCE = checkout("manager")
PACKAGED = bundled("manager", "default.xbe")
PACKAGED_LAUNCHER = bundled("manager", "launcher.xbe")
INBOX = "E:/TES3X/update"
FOLDER = "TES3XManager"
TITLE = "TES3X Manager"
PROFILE = "manager"
# The manager does not list a folder of this layout among the builds.
LAYOUT = "manager"


class ManagerError(Exception):
    pass


def version():
    match = re.search(r'#define MGR_VERSION "([^"]+)"',
                      (resource("manager", "mgr.h")).read_text(encoding="utf-8"))
    return match.group(1) if match else None


def find_xbe(given=None, config=None, work=None, launcher=False):
    """The manager XBE, or with `launcher` the launcher's: given, packaged, else built."""
    if given:
        if not Path(given).is_file():
            raise ManagerError(f"not found: {given}")
        return Path(given)
    packaged, source, name = ((PACKAGED_LAUNCHER, SOURCE / "launcher", "launcher") if launcher
                              else (PACKAGED, SOURCE, "app"))
    if packaged.is_file():
        return packaged
    out = Path(work or Path.cwd()) / "build" / "manager" / name
    print(f"building the manager{' launcher' if launcher else ''} in {out}", flush=True)
    try:
        return tes3x_nxdk.build(source, out, config=config)
    except tes3x_nxdk.NxdkError as exc:
        raise ManagerError(f"no {'launcher' if launcher else 'manager'} XBE: {exc}") from exc


def remote_folder(target, folder=FOLDER):
    games = target.get("games_root")
    if not games:
        raise ManagerError(f"target {target['name']!r} needs games_root")
    return xbox_root(games).rstrip("/") + "/" + folder


def stage(out, xbe, launcher, remote, agent=None):
    """Write the manager's deploy tree and console.ini under `out`; returns the tree. `agent` is
    the NetAgent value naming this PC, so the manager pairs without a game deploy first."""
    out = Path(out)
    tree = out / "deploy"
    if tree.exists():
        shutil.rmtree(tree)
    (tree / "_resources").mkdir(parents=True)
    (tree / "a").mkdir()
    shutil.copyfile(launcher, tree / "default.xbe")
    shutil.copyfile(xbe, tree / "a" / "default.xbe")
    title_id = Xbe(Path(launcher).read_bytes()).cert()["title_id"]
    folder = remote.rsplit("/", 1)[-1]
    (tree / "_resources" / "default.xml").write_text(dashboard_xml(TITLE, folder, title_id),
                                                     encoding="utf-8")
    manifest = tes3x_manifest.create(
        tree, profile=PROFILE, source={"kind": "manager", "version": version()},
        install_layout=LAYOUT)
    # Not rebuilt from a retail image, whatever their suffix says.
    for name in ("default.xbe", "a/default.xbe"):
        manifest["files"][name]["origin"] = "build"
    tes3x_manifest.write(tree, manifest)
    # Deploy takes the folder's owner from this; a manager folder belongs to no other profile.
    (out / tes3x_deploy.PIPELINE_MARKER).write_text(
        json.dumps({"profile": PROFILE, "install_layout": LAYOUT, "remote": remote,
                    "version": version()}) + "\n", encoding="utf-8")
    path = remote.replace("/", "\\") + "\\default.xbe"
    (out / "console.ini").write_text(
        f"[Xbox]\r\nManager={path}\r\n" + (f"NetAgent={agent}\r\n" if agent else ""),
        encoding="latin-1")
    return tree


def stage_xemu(out, xbe, vanilla=None):
    """What an xemu session needs to start in the manager: OUT/disc, a disc folder whose
    default.xbe is the manager, and OUT/seed, the files the session's disk gets once (a retail
    base for the manager to build on, and a console.ini that brings the NIC up by DHCP)."""
    out = Path(out)
    disc, seed = out / "disc", out / "seed"
    shutil.rmtree(disc, ignore_errors=True)
    disc.mkdir(parents=True)
    shutil.copyfile(xbe, disc / "default.xbe")
    # an ISO needs a second file
    (disc / "readme.txt").write_text("TES3X manager for xemu\n", encoding="utf-8")
    seed.mkdir(parents=True, exist_ok=True)
    (seed / "console.ini").write_bytes(b"[Xbox]\r\nNetAddress=dhcp\r\n")
    base = seed / "Games" / "Base"
    if vanilla and not base.is_dir():
        from tes3x.pipeline import stage_retail_base
        from tes3x.xemu import link_or_copy
        stage_retail_base(vanilla, base, link_or_copy)
        tes3x_manifest.write(base, tes3x_manifest.create(
            base, profile="TES3X retail base", source={"kind": "xemu"},
            install_layout="retail-base"))
    # a retail copy has its INI; stage_retail_base leaves it out, and a base staged before
    # this still lacks it, so it is seeded as a file of its own
    if vanilla and base.is_dir() and not (base / "Morrowind.ini").is_file():
        shutil.copyfile(Path(vanilla) / "Morrowind.ini", base / "Morrowind.ini")
    return disc, seed


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


def send_update(args):
    """Send a signed release to the console's update folder; through the agent, restart the
    manager so it installs it."""
    try:
        release = tes3x_release.check_release(args.release, args.public)
    except tes3x_release.ReleaseError as exc:
        raise ManagerError(f"{args.release}: {exc}") from exc
    tes3x_ftp.resolve(args)
    remote = remote_folder(tes3x_targets.resolve(
        tes3x_ftp.local_settings(args.config), args.target, "xbox", required=True), args.folder)
    if args.agent:
        args.agent_key = args.agent_key or str(
            local_config(args.config).with_name("tes3x.agent.key"))
        print(f"waiting for the manager at {args.host} to pair", flush=True)
    try:
        target = tes3x_deploy.AgentTarget(args) if args.agent else tes3x_deploy.FtpTarget(args)
    except tes3x_agent.AgentError as exc:
        raise ManagerError(str(exc)) from exc
    try:
        # release.json last: the manager keeps a release whose files are still arriving
        for name in (*tes3x_release.RELEASE_FILES, tes3x_release.RELEASE + ".sig",
                     tes3x_release.RELEASE):
            target.write(f"{INBOX}/{name}", (Path(args.release) / name).read_bytes())
        print(f"manager {release['version']} sent to {INBOX}", flush=True)
        if args.agent:
            launcher = remote.replace("/", "\\") + "\\default.xbe"
            target.client.call(*tes3x_agent.path_request(tes3x_agent.OP_LAUNCH, launcher),
                               f"launch {launcher}")
            print("the manager restarts and installs it")
        else:
            print("it is installed the next time the manager starts")
    except tes3x_agent.AgentError as exc:
        raise ManagerError(f"agent: {exc}") from exc
    finally:
        target.close()


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
        p.add_argument("--launcher", help="the launcher XBE (default: packaged, else built)")
        p.add_argument("--folder", default=FOLDER, help=f"folder under games_root ({FOLDER})")
        p.add_argument("--config", help=CONFIG_HELP)
        p.add_argument("--target", help="Xbox target (default: default_target)")
        p.add_argument("--no-agent", action="store_true",
                       help="leave the console's NetAgent alone instead of naming this PC")
    p = sub.add_parser("xemu", help="stage the manager as an xemu disc and the files its disk "
                                    "is seeded with")
    p.add_argument("out", help="folder for disc/ and seed/")
    p.add_argument("--xbe", help="the manager XBE (default: packaged, else built)")
    p.add_argument("--config", help=CONFIG_HELP)
    p = sub.add_parser("update")
    p.add_argument("release", help="a signed release folder (tes3x release manager)")
    p.add_argument("--agent", action="store_true",
                   help="send through the running manager's agent and restart it, instead of FTP")
    p.add_argument("--public", default=str(tes3x_release.PUBLIC),
                   help="the release key the manager embeds, hex or file (default: keys/release.pub)")
    p.add_argument("--agent-key", help="this PC's agent key (default: tes3x.agent.key beside "
                                       "the local config)")
    p.add_argument("--agent-port", type=int, default=tes3x_agent.PORT)
    p.add_argument("--agent-wait", type=float, default=60)
    p.add_argument("--retries", type=int, default=2)
    p.add_argument("--folder", default=FOLDER, help=f"folder under games_root ({FOLDER})")
    tes3x_ftp.add_arguments(p)
    args = ap.parse_args()

    if args.command == "update":
        try:
            send_update(args)
        except ManagerError as exc:
            sys.exit(str(exc))
        return
    if args.command == "xemu":
        try:
            xbe = find_xbe(args.xbe, args.config)
        except ManagerError as exc:
            sys.exit(str(exc))
        root = tes3x_ftp.local_settings(args.config).get("paths", {}).get("vanilla_root")
        base = Path(args.config).parent if args.config else Path.cwd()
        vanilla = (base / root) if root else None
        disc, seed = stage_xemu(args.out, xbe, vanilla if vanilla and vanilla.is_dir() else None)
        print(f"manager {version()} staged for xemu in {disc}"
              + ("" if vanilla else "; no [paths] vanilla_root, so no retail base"), flush=True)
        return
    local = tes3x_ftp.local_settings(args.config)
    try:
        target = tes3x_targets.resolve(local, args.target, "xbox", required=True)
        remote = remote_folder(target, args.folder)
        xbe = find_xbe(args.xbe, args.config)
        launcher = find_xbe(args.launcher, args.config, launcher=True)
        base = Path(args.config).parent if args.config else Path.cwd()
        agent = None if args.no_agent else agent_setting(base, target)
    except (tes3x_targets.TargetError, ManagerError, PipelineError) as exc:
        sys.exit(str(exc))
    out = Path(getattr(args, "out", None) or Path.cwd() / "build" / "manager" / "install")
    stage(out, xbe, launcher, remote, agent)
    print(f"manager {version()} staged for {remote}", flush=True)
    if args.command == "install":
        args.target = target["name"]
        sys.exit(subprocess.call([sys.executable, "-m", "tes3x", "deploy",
                                  *deploy_arguments(out, remote, args)]))


if __name__ == "__main__":
    main()
