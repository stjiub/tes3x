"""Xbox FTP settings shared by deploy, fetch and diag.

Each setting comes from the command line first, then the [deploy] table of tes3x.local.toml, then
the dashboard default. The password can also come from the TES3X_FTP_PASSWORD environment
variable, which beats the config file, or be typed at a prompt with --ask-password.
"""

import ftplib
import getpass
import os
from pathlib import Path
import tomllib

DEFAULT_PORT = 21
DEFAULT_USER = "xbox"
DEFAULT_PASSWORD = "xbox"
PASSWORD_ENV = "TES3X_FTP_PASSWORD"


def add_arguments(parser):
    parser.add_argument("--host", help="the Xbox's IP address (default: deploy.host)")
    parser.add_argument("--port", type=int, help=f"(default: deploy.port, then {DEFAULT_PORT})")
    parser.add_argument("--user", help=f"(default: deploy.user, then {DEFAULT_USER})")
    parser.add_argument("--password", help=f"(default: {PASSWORD_ENV}, deploy.password, "
                                           f"then {DEFAULT_PASSWORD})")
    parser.add_argument("--ask-password", action="store_true", help="type the password at a prompt")
    parser.add_argument("--config", help="local config (default: ./tes3x.local.toml)")


def local_settings(config=None):
    path = Path(config) if config else Path.cwd() / "tes3x.local.toml"
    if not path.is_file():
        if config:
            raise SystemExit(f"config not found: {path}")
        return {}
    with open(path, "rb") as stream:
        return tomllib.load(stream).get("deploy", {})


def resolve(args, environ=os.environ):
    """Fill host, port, user and password on args in place."""
    settings = local_settings(args.config)
    args.host = args.host or settings.get("host")
    if not args.host:
        raise SystemExit("no Xbox address: pass --host or set deploy.host in tes3x.local.toml")
    args.port = args.port or settings.get("port", DEFAULT_PORT)
    args.user = args.user or settings.get("user", DEFAULT_USER)
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
