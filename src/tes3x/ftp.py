"""Xbox FTP settings shared by deploy, fetch and diag.

Each setting comes from the command line first, then the selected Xbox target in
tes3x.local.toml, then the dashboard default. The password can also come from the
TES3X_FTP_PASSWORD environment
variable, which beats the config file, or be typed at a prompt with --ask-password.
"""

import ftplib
import getpass
import os
import tomllib

from tes3x.paths import local_config
import tes3x.targets as tes3x_targets

DEFAULT_PORT = 21
DEFAULT_USER = "xbox"
DEFAULT_PASSWORD = "xbox"
PASSWORD_ENV = "TES3X_FTP_PASSWORD"


def add_arguments(parser):
    parser.add_argument("--target", help="Xbox target (default: default_target)")
    parser.add_argument("--host", help="the Xbox's IP address (default: target host)")
    parser.add_argument("--port", type=int, help=f"(default: target port, then {DEFAULT_PORT})")
    parser.add_argument("--user", help=f"(default: target user, then {DEFAULT_USER})")
    parser.add_argument("--password", help=f"(default: {PASSWORD_ENV}, target password, "
                                           f"then {DEFAULT_PASSWORD})")
    parser.add_argument("--ask-password", action="store_true", help="type the password at a prompt")
    parser.add_argument("--config", help="local config (default: see docs/configuration.md)")


def local_settings(config=None):
    path = local_config(config)
    if not path.is_file():
        if config:
            raise SystemExit(f"config not found: {path}")
        return {}
    with open(path, "rb") as stream:
        return tomllib.load(stream)


def resolve(args, environ=os.environ):
    """Fill host, port, user and password on args in place."""
    local = local_settings(args.config)
    try:
        settings = tes3x_targets.resolve(local, args.target, "xbox",
                                         required=not bool(args.host)) or {}
    except tes3x_targets.TargetError as exc:
        raise SystemExit(str(exc)) from exc
    args.target = settings.get("name", args.target)
    args.host = args.host or settings.get("host")
    if not args.host:
        raise SystemExit("no Xbox address: pass --host or configure an Xbox target")
    args.port = args.port or settings.get("port", DEFAULT_PORT)
    args.user = args.user or settings.get("user", DEFAULT_USER)
    args.agent_token = settings.get("agent_token")
    if args.ask_password:
        args.password = getpass.getpass(f"FTP password for {args.user}@{args.host}: ")
    else:
        args.password = (args.password or environ.get(PASSWORD_ENV)
                         or settings.get("password") or DEFAULT_PASSWORD)
    return args


def connect(args):
    # The console's names are latin-1; decoding them as UTF-8 fails on some saves.
    ftp = ftplib.FTP(encoding="latin-1")
    ftp.connect(args.host, args.port, timeout=30)
    ftp.login(args.user, args.password)
    return ftp
