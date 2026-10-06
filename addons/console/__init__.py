"""Start builds on a real Xbox through a small agent in the XBMC4Gamers dashboard."""

from pathlib import Path

import tes3x_targets

HERE = Path(__file__).resolve().parent
TOOLS = HERE.parents[1] / "tools"
LABEL = "Xbox dashboard agent (XBMC4Gamers)"
DESCRIPTION = ("Adds Play on Xbox: deploy the build over FTP and start it through an agent in the "
               "XBMC4Gamers dashboard. Manage the agent on each Xbox target.")
PLAY = {"xbox": ("Xbox", " on Xbox")}
# Prints the agent's "ok C=free/total ..." (MB) for the status bar's drive badge.
DRIVE_SPACE = (HERE / "console.py", ["drives"])
AGENT_STATUS = (HERE / "console.py", ["ping"], 6)


def status(local):
    try:
        target = tes3x_targets.resolve(local, kind="xbox", required=True)
    except tes3x_targets.TargetError as exc:
        return str(exc)
    if not target.get("host"):
        return "Set the Xbox target's address in Targets > Setup"
    return None


def target_and_root(plain, local, name=None):
    target = tes3x_targets.resolve(local, name, "xbox", required=True)
    return target, tes3x_targets.remote_root(plain, target)


def play_steps(key, context):
    """Check the agent answers, deploy the build, then start it."""
    target, remote = target_and_root(context["plain"], context["local"],
                                     context.get("target"))
    config = ["--config", str(context["config"])]
    selected = ["--target", target["name"]]
    deploy = [str(context["deploy"]), "--remote", remote, "--verify", "size", *config,
              *selected]
    if context["plain"].get("rules", {}).get("clear_cache_partitions", False):
        deploy.append("--clear-cache")
    console_ini = Path(context["deploy"]).parent / "console.ini"
    if console_ini.is_file():
        deploy += ["--console-ini", str(console_ini)]
    console = HERE / "console.py"
    return [(console, ["ping", *config, *selected], "Looking for the Xbox dashboard agent…"),
            (TOOLS / "tes3x_deploy.py", deploy, f"Deploying to {remote}…"),
            (console, ["run", remote.rstrip("/") + "/default.xbe", *config, *selected],
             "Starting the game on the Xbox…")]
