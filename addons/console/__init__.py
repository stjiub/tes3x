"""Start builds on a real Xbox through a small agent in the XBMC4Gamers dashboard."""

from pathlib import Path

HERE = Path(__file__).resolve().parent
TOOLS = HERE.parents[1] / "tools"
LABEL = "Xbox dashboard agent (XBMC4Gamers)"
DESCRIPTION = ("Adds Play on Xbox: deploy the build over FTP and start it through an agent in the "
               "XBMC4Gamers dashboard. Install the agent from here once.")
PLAY = {"xbox": ("Xbox", " on Xbox")}
SETTINGS_ACTIONS = [("Install agent on Xbox", HERE / "console.py", ["install"]),
                    ("Remove agent", HERE / "console.py", ["uninstall"])]
# Prints the agent's "ok C=free/total ..." (MB) for the status bar's drive badge.
DRIVE_SPACE = (HERE / "console.py", ["drives"])


def status(local):
    if not local.get("deploy", {}).get("host"):
        return "Set the Xbox address in File > Settings"
    return None


def remote_root(plain, local):
    return (plain.get("profile", {}).get("remote_root")
            or local.get("deploy", {}).get("remote_root"))


def play_steps(key, context):
    """Check the agent answers, deploy the build, then start it."""
    remote = remote_root(context["plain"], context["local"])
    if not remote:
        raise ValueError("Set the Xbox folder in the Build tab or File > Settings")
    config = ["--config", str(context["config"])]
    deploy = [str(context["deploy"]), "--remote", remote, "--verify", "size", *config]
    if context["plain"].get("rules", {}).get("clear_cache_partitions", False):
        deploy.append("--clear-cache")
    console = HERE / "console.py"
    return [(console, ["ping", *config], "Looking for the Xbox dashboard agent…"),
            (TOOLS / "tes3x_deploy.py", deploy, f"Deploying to {remote}…"),
            (console, ["run", remote.rstrip("/") + "/default.xbe", *config],
             "Starting the game on the Xbox…")]
