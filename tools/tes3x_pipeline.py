#!/usr/bin/env python3
"""Build, patch, pack and optionally deploy one TES3X profile."""

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import tomllib


ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
HOOKS = ROOT / "hooks"
MARKER = ".tes3x-pipeline.json"

PATCHES = {
    "mcp-1": {"category": "core", "status": "implemented", "source": "tes3xrefs.c"},
    "mcp-97": {"category": "core", "status": "implemented", "source": "tes3xmcp97.c"},
    "mcp-154": {"category": "core", "status": "implemented", "source": "tes3xmcp154.c"},
    "script-ext": {"category": "compat", "status": "verified-xemu", "source": "tes3xscript.c"},
    "diagnostics": {
        "category": "instrumentation", "status": "verified-xemu", "source": "tes3xdiag.c"
    },
    "console": {"category": "qol", "status": "verified-xemu", "source": "tes3xconsole.c"},
    "rotating-autosaves": {
        "category": "qol", "status": "implemented", "source": "tes3xsaves.c"
    },
}
HOOK_SOURCES = {
    "multi-bsa": "tes3xarch.c",
    **{name: meta["source"] for name, meta in PATCHES.items()},
}
PATCH_ORDER = ("multi-bsa", "script-ext", "mcp-1", "mcp-97", "mcp-154",
               "rotating-autosaves", "diagnostics", "console")
CATEGORIES = {"core", "correctness", "compat", "performance", "instrumentation", "qol", "balance"}
VERIFIED = {"verified-xemu", "verified-hardware"}
PRESETS = ("minimal", "standard", "development")


class PipelineError(ValueError):
    pass


def read_toml(path):
    with open(path, "rb") as stream:
        return tomllib.load(stream)


def string_list(value, field):
    if value is None:
        return []
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise PipelineError(f"{field} must be an array of strings")
    return value


def resolve_patch_plan(profile, preset_override=None, enable=(), disable=(), package_mode=None):
    """Resolve user-facing patches and packaging-derived infrastructure."""
    config = profile.get("patches", {})
    preset = preset_override or config.get("preset", "standard")
    if preset not in PRESETS:
        raise PipelineError(f"unknown patch preset {preset!r}; choose from {', '.join(PRESETS)}")

    selected = set()
    if preset in {"standard", "development"}:
        selected.update(name for name, meta in PATCHES.items()
                        if meta["category"] in {"core", "correctness"}
                        and meta["status"] in VERIFIED)
    if preset == "development":
        selected.update(name for name, meta in PATCHES.items()
                        if meta["category"] == "instrumentation")
        selected.add("console")

    categories = set(string_list(config.get("categories"), "patches.categories"))
    unknown_categories = categories - CATEGORIES
    if unknown_categories:
        raise PipelineError("unknown patch categories: " + ", ".join(sorted(unknown_categories)))
    selected.update(name for name, meta in PATCHES.items() if meta["category"] in categories)

    enabled = set(string_list(config.get("enable"), "patches.enable")) | set(enable)
    disabled = set(string_list(config.get("disable"), "patches.disable")) | set(disable)
    unknown = (enabled | disabled) - PATCHES.keys()
    if unknown:
        raise PipelineError("unknown selectable patches: " + ", ".join(sorted(unknown)))
    overlap = enabled & disabled
    if overlap:
        raise PipelineError("patches both enabled and disabled: " + ", ".join(sorted(overlap)))
    selected.update(enabled)
    selected.difference_update(disabled)

    mode = package_mode or profile.get("package", {}).get("mode", "delta-bsa")
    if mode not in {"delta-bsa", "merged-bsa"}:
        raise PipelineError("package.mode must be 'delta-bsa' or 'merged-bsa'")
    applied = set(selected)
    if mode == "delta-bsa":
        applied.add("multi-bsa")

    sources = ["tes3xhook.c", "tes3xlog.c"]
    for name in PATCH_ORDER:
        source = HOOK_SOURCES.get(name)
        if name in applied and source and source not in sources:
            sources.append(source)
    return {
        "preset": preset,
        "selected": [name for name in PATCH_ORDER if name in selected],
        "applied": [name for name in PATCH_ORDER if name in applied],
        "sources": sources,
        "package_mode": mode,
        "needs_payload": bool(applied),
    }


def config_path(value, base):
    path = Path(value).expanduser()
    return path if path.is_absolute() else base / path


def require_file(path, label):
    if not path.is_file():
        raise PipelineError(f"{label} not found: {path}")


def run(command, env=None, display=None):
    shown = display if display is not None else command
    print("\n== " + " ".join(str(part) for part in shown), flush=True)
    subprocess.run([str(part) for part in command], check=True, env=env)


def find_bash(value=None):
    if value:
        path = Path(value)
        if path.is_file():
            return str(path)
        found = shutil.which(value)
        if found:
            return found
        raise PipelineError(f"bash not found: {value}")
    if os.name == "nt":
        for candidate in (Path("C:/msys64/usr/bin/bash.exe"), Path("C:/Program Files/Git/bin/bash.exe")):
            if candidate.is_file():
                return str(candidate)
    found = shutil.which("bash")
    if not found:
        raise PipelineError("bash not found; set paths.bash or pass --bash")
    return found


def validate_output(path):
    resolved = path.resolve()
    if resolved == Path(resolved.anchor) or resolved == Path.cwd().resolve():
        raise PipelineError(f"refusing to use broad output path: {resolved}")
    if path.exists():
        if not path.is_dir():
            raise PipelineError(f"output is not a directory: {path}")
        if any(path.iterdir()) and not (path / MARKER).is_file():
            raise PipelineError(f"existing output is not owned by tes3x_pipeline: {path}")


def publish(work, output):
    """Replace only an empty or pipeline-owned output after a complete build."""
    validate_output(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    backup = output.parent / ("." + output.name + ".previous")
    if backup.exists():
        if not (backup / MARKER).is_file():
            raise PipelineError(f"refusing to replace unowned backup: {backup}")
        shutil.rmtree(backup)
    if output.exists():
        os.replace(output, backup)
    try:
        os.replace(work, output)
    except Exception:
        if backup.exists() and not output.exists():
            os.replace(backup, output)
        raise
    if backup.exists():
        shutil.rmtree(backup)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("profile")
    ap.add_argument("--config", help="local paths and Xbox settings (default: ./tes3x.local.toml if present)")
    ap.add_argument("--vanilla", help="clean retail game root containing Data Files and both XBEs")
    ap.add_argument("--nxdk", help="nxdk checkout (required while payloads are built locally)")
    ap.add_argument("--build-root", help="parent for profile outputs")
    ap.add_argument("--out", help="complete pipeline output (default: BUILD_ROOT/PROFILE_NAME)")
    ap.add_argument("--preset", choices=PRESETS)
    ap.add_argument("--enable", action="append", default=[], metavar="PATCH")
    ap.add_argument("--disable", action="append", default=[], metavar="PATCH")
    ap.add_argument("--drive", help="game-directory drive letter (default: D)")
    ap.add_argument("--bash", help="MSYS/Git Bash used for hooks/build.sh")
    action = ap.add_mutually_exclusive_group()
    action.add_argument("--deploy", action="store_true", help="build and deploy to the configured Xbox")
    action.add_argument("--dry-run", action="store_true", help="build, then show the Xbox deployment diff")
    ap.add_argument("--plan", action="store_true", help="show resolved work without building")
    args = ap.parse_args(argv)

    profile_path = Path(args.profile).resolve()
    require_file(profile_path, "profile")
    profile = read_toml(profile_path)
    profile_name = profile.get("profile", {}).get("name")
    if not profile_name or not isinstance(profile_name, str):
        raise PipelineError("profile.name is required")

    if args.config:
        local_path = Path(args.config).resolve()
    else:
        candidate = Path.cwd() / "tes3x.local.toml"
        local_path = candidate if candidate.is_file() else None
    local = read_toml(local_path) if local_path else {}
    base = local_path.parent if local_path else Path.cwd()
    paths = local.get("paths", {})
    deploy = local.get("deploy", {})

    vanilla_value = args.vanilla or paths.get("vanilla_root")
    if not vanilla_value:
        raise PipelineError("set paths.vanilla_root in local config or pass --vanilla")
    vanilla = config_path(vanilla_value, base).resolve()
    data_files = vanilla / "Data Files"
    retail_xbe = vanilla / "morrowind.xbe"
    launcher = vanilla / "Default.xbe"
    ini = vanilla / "Morrowind.ini"
    for path, label in ((retail_xbe, "retail morrowind.xbe"), (launcher, "retail Default.xbe"),
                        (ini, "retail Morrowind.ini")):
        require_file(path, label)
    if not data_files.is_dir():
        raise PipelineError(f"retail Data Files not found: {data_files}")

    plan = resolve_patch_plan(profile, args.preset, args.enable, args.disable)
    package = profile.get("package", {})
    drive = (args.drive or package.get("drive_letter", "D")).upper()
    if len(drive) != 1 or not drive.isalpha():
        raise PipelineError("package.drive_letter must be one letter")
    remote = deploy.get("remote_root") or profile.get("profile", {}).get("remote_root")
    build_value = args.build_root or paths.get("build_root", "build")
    build_root = config_path(build_value, base).resolve()
    output = Path(args.out).resolve() if args.out else build_root / profile_name
    validate_output(output)

    print(f"profile: {profile_name}")
    print(f"preset: {plan['preset']}")
    print("patches: " + (", ".join(plan["applied"]) or "none"))
    print(f"assets: {plan['package_mode']}")
    print(f"output: {output}")
    if args.deploy or args.dry_run:
        print(f"target: {deploy.get('host', '<missing>')} {remote or '<missing>'}")
    if args.plan:
        return 0

    nxdk_value = args.nxdk or paths.get("nxdk_dir")
    if plan["needs_payload"] and not nxdk_value:
        raise PipelineError("selected patches require paths.nxdk_dir or --nxdk")
    nxdk = config_path(nxdk_value, base).resolve() if nxdk_value else None
    if nxdk and not nxdk.is_dir():
        raise PipelineError(f"nxdk checkout not found: {nxdk}")
    bash = find_bash(args.bash or paths.get("bash")) if plan["needs_payload"] else None
    if (args.deploy or args.dry_run) and (not deploy.get("host") or not remote):
        raise PipelineError("deployment requires deploy.host and deploy.remote_root")

    build_root.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix=f".{profile_name}-", dir=build_root))
    tree = work / "tree"
    manifest = work / "manifest.json"
    hook_out = work / "hooks"
    patched = work / "morrowind.xbe"
    staged = work / "deploy"
    try:
        build_cmd = [sys.executable, TOOLS / "tes3x_build.py", profile_path,
                     "--out", tree, "--json", manifest, "--vanilla", data_files]
        if remote:
            build_cmd += ["--remote-root", remote]
        run(build_cmd)

        payload = hook_out / "tes3xhook.pe"
        if plan["needs_payload"]:
            env = os.environ.copy()
            env.update({"NXDK_DIR": str(nxdk), "OUT": str(hook_out),
                        "SRCS": " ".join(plan["sources"])})
            if os.name == "nt":
                env["PATH"] = str(Path(bash).parent) + os.pathsep + env.get("PATH", "")
            run([bash, HOOKS / "build.sh", retail_xbe, hook_out / "injected-check.xbe"], env)

        patch_specs = []
        if plan["needs_payload"]:
            patch_specs.append(f"payload={payload}")
        patch_specs.extend(("boot-media", f"drive-letters={drive}"))
        patch_specs.extend(plan["applied"])
        patch_cmd = [sys.executable, TOOLS / "tes3x_patch.py", retail_xbe]
        for spec in patch_specs:
            patch_cmd += ["--apply", spec]
        patch_cmd += ["--out", patched]
        run(patch_cmd)

        pack_cmd = [sys.executable, TOOLS / "tes3x_pack.py", tree,
                    "--vanilla", data_files, "--ini", ini, "--out", staged]
        if plan["package_mode"] == "delta-bsa":
            pack_cmd += ["--delta-archive", package.get("archive_name", "tes3xmods.bsa")]
        if package.get("archive_only", False):
            pack_cmd.append("--archive-only")
        if remote:
            pack_cmd += ["--remote-root", remote]
        run(pack_cmd)

        shutil.copy2(launcher, staged / "Default.xbe")
        shutil.copy2(patched, staged / "morrowind.xbe")
        record = {
            "profile": profile_name,
            "profile_path": str(profile_path),
            "preset": plan["preset"],
            "patches": ["payload=hooks/tes3xhook.pe" if spec.startswith("payload=") else spec
                        for spec in patch_specs],
            "package_mode": plan["package_mode"],
            "deploy_tree": "deploy",
        }
        (work / MARKER).write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
        publish(work, output)
    except Exception:
        print(f"\nbuild failed; partial output kept at {work}", file=sys.stderr)
        raise

    print(f"\ncomplete install staged at {output / 'deploy'}")
    if args.deploy or args.dry_run:
        deploy_cmd = [sys.executable, TOOLS / "tes3x_deploy.py", output / "deploy",
                      "--host", deploy["host"], "--remote", remote]
        for key, flag in (("port", "--port"), ("user", "--user"), ("password", "--password")):
            if key in deploy:
                deploy_cmd += [flag, str(deploy[key])]
        if args.dry_run:
            deploy_cmd.append("--dry-run")
        if profile.get("rules", {}).get("clear_cache_partitions", False) and args.deploy:
            deploy_cmd.append("--clear-cache")
        shown = list(deploy_cmd)
        if "--password" in shown:
            shown[shown.index("--password") + 1] = "***"
        run(deploy_cmd, display=shown)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (PipelineError, subprocess.CalledProcessError) as exc:
        raise SystemExit(str(exc))
