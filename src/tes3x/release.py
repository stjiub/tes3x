#!/usr/bin/env python3
"""The project's release signing key (Ed25519), which the console manager checks updates against.

    tes3x release keygen KEY            write a new private key; prints its public key
    tes3x release protect KEY           set or change the passphrase of a private key
    tes3x release public KEY            print the public key of a private key
    tes3x release sign KEY FILE         write FILE.sig (64 bytes)
    tes3x release verify PUBLIC FILE    check FILE.sig against a public key (hex or file)
    tes3x release manager OUT --key KEY --manager XBE --launcher XBE
                                           write a signed manager release into OUT
    tes3x release check DIR             check a manager release against keys/release.pub

The private key never goes into a repository. A key with a passphrase asks for it, or reads
TES3X_RELEASE_PASSPHRASE. The public key the manager embeds is keys/release.pub; a fork makes
its own key and replaces that file.
"""

import argparse
import getpass
import hashlib
import json
import os
import re
import sys
from pathlib import Path

from tes3x.paths import resource

PUBLIC = resource("keys", "release.pub")
PASSPHRASE_ENV = "TES3X_RELEASE_PASSPHRASE"

# A manager release, as the manager's update.c reads it.
RELEASE = "release.json"
RELEASE_FORMAT = 1
PRODUCT = "tes3x-manager"
RELEASE_FILES = ("manager.xbe", "launcher.xbe")
VERSION_TAG = re.compile(rb"TES3X-MANAGER-VERSION ([0-9]+(?:\.[0-9]+)*)\0")


class ReleaseError(Exception):
    pass


def _ed25519():
    try:
        from cryptography.hazmat.primitives.asymmetric import ed25519
        from cryptography.hazmat.primitives import serialization
    except ImportError:
        raise SystemExit("release signing needs the cryptography package") from None
    return ed25519, serialization


def public_bytes(private):
    _, serialization = _ed25519()
    return private.public_key().public_bytes(serialization.Encoding.Raw,
                                             serialization.PublicFormat.Raw)


def fingerprint(public):
    return hashlib.sha256(public).hexdigest()[:16]


def ask_new_passphrase():
    first = getpass.getpass("new passphrase (empty for none): ")
    if first != getpass.getpass("again: "):
        raise SystemExit("the passphrases differ")
    return first


def load_private(path, passphrase=None):
    _, serialization = _ed25519()
    data = Path(path).read_bytes()
    try:
        return serialization.load_pem_private_key(data, password=None)
    except TypeError:
        pass
    if passphrase is None:
        passphrase = os.environ.get(PASSPHRASE_ENV) or getpass.getpass(f"passphrase for {path}: ")
    try:
        return serialization.load_pem_private_key(data, password=passphrase.encode())
    except ValueError:
        raise SystemExit(f"wrong passphrase for {path}") from None


def private_pem(private, passphrase):
    _, serialization = _ed25519()
    encryption = (serialization.BestAvailableEncryption(passphrase.encode()) if passphrase
                  else serialization.NoEncryption())
    return private.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                 encryption)


def load_public(text):
    ed25519, _ = _ed25519()
    path = Path(text)
    value = path.read_text(encoding="ascii").split()[0] if path.is_file() else text
    return ed25519.Ed25519PublicKey.from_public_bytes(bytes.fromhex(value))


def keygen(path, passphrase):
    ed25519, _ = _ed25519()
    path = Path(path)
    if path.exists():
        raise SystemExit(f"{path} exists; a release key is never replaced by accident")
    private = ed25519.Ed25519PrivateKey.generate()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "xb") as out:
        out.write(private_pem(private, passphrase))
    return public_bytes(private)


def protect(path, private, passphrase):
    """Rewrite a private key under a new passphrase (empty for none); the key stays the same."""
    staged = Path(str(path) + ".new")
    staged.write_bytes(private_pem(private, passphrase))
    os.replace(staged, path)
    return public_bytes(private)


def manager_version(xbe):
    match = VERSION_TAG.search(xbe)
    if not match:
        raise ReleaseError("no manager version in the XBE; is it the manager?")
    return match.group(1).decode()


def make_manager_release(out, key, manager, launcher, passphrase=None):
    """Write release.json, its signature and the two XBEs into `out`; returns the version."""
    data = {"manager.xbe": Path(manager).read_bytes(), "launcher.xbe": Path(launcher).read_bytes()}
    version = manager_version(data["manager.xbe"])
    if VERSION_TAG.search(data["launcher.xbe"]):
        raise ReleaseError("the launcher given is a manager")
    release = {"format": RELEASE_FORMAT, "product": PRODUCT, "version": version,
               "files": {name: {"size": len(d), "sha256": hashlib.sha256(d).hexdigest()}
                         for name, d in data.items()}}
    text = (json.dumps(release, indent=1, sort_keys=True) + "\n").encode()
    signature = load_private(key, passphrase).sign(text)
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    for name, d in data.items():
        (out / name).write_bytes(d)
    (out / RELEASE).write_bytes(text)
    (out / (RELEASE + ".sig")).write_bytes(signature)
    return version


def check_release(folder, public=PUBLIC):
    """The release in `folder` as a dict, after the checks the manager makes; ReleaseError if not."""
    from cryptography.exceptions import InvalidSignature
    folder = Path(folder)
    try:
        text = (folder / RELEASE).read_bytes()
        signature = (folder / (RELEASE + ".sig")).read_bytes()
    except OSError as exc:
        raise ReleaseError(f"not a release: {exc}") from None
    try:
        load_public(str(public)).verify(signature, text)
    except InvalidSignature:
        raise ReleaseError("the signature is not the release key's") from None
    release = json.loads(text)
    if release.get("format") != RELEASE_FORMAT or release.get("product") != PRODUCT:
        raise ReleaseError("not a manager release this version reads")
    for name in RELEASE_FILES:
        entry = release.get("files", {}).get(name)
        path = folder / name
        if not entry or not path.is_file():
            raise ReleaseError(f"{name} is missing")
        d = path.read_bytes()
        if len(d) != entry["size"] or hashlib.sha256(d).hexdigest() != entry["sha256"]:
            raise ReleaseError(f"{name} does not match the release")
    return release


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="command", required=True)
    p = sub.add_parser("keygen")
    p.add_argument("key")
    p.add_argument("--no-passphrase", action="store_true",
                   help="leave the key unencrypted (for tests)")
    sub.add_parser("protect").add_argument("key")
    sub.add_parser("public").add_argument("key")
    p = sub.add_parser("sign")
    p.add_argument("key")
    p.add_argument("file")
    p = sub.add_parser("verify")
    p.add_argument("public", nargs="?", default=str(PUBLIC))
    p.add_argument("file")
    p = sub.add_parser("manager")
    p.add_argument("out")
    p.add_argument("--key", required=True, help="the private release key")
    p.add_argument("--manager", required=True, help="the manager XBE")
    p.add_argument("--launcher", required=True, help="the launcher XBE")
    p = sub.add_parser("check")
    p.add_argument("folder")
    p.add_argument("--public", default=str(PUBLIC))
    args = ap.parse_args()

    try:
        if args.command in ("keygen", "protect", "public"):
            if args.command == "keygen":
                public = keygen(args.key, "" if args.no_passphrase else ask_new_passphrase())
            elif args.command == "protect":
                private = load_private(args.key)
                public = protect(args.key, private, ask_new_passphrase())
            else:
                public = public_bytes(load_private(args.key))
            print(f"{public.hex()}  fingerprint {fingerprint(public)}")
        elif args.command == "sign":
            signature = load_private(args.key).sign(Path(args.file).read_bytes())
            Path(args.file + ".sig").write_bytes(signature)
            print(f"wrote {args.file}.sig")
        elif args.command == "verify":
            from cryptography.exceptions import InvalidSignature
            try:
                load_public(args.public).verify(Path(args.file + ".sig").read_bytes(),
                                                Path(args.file).read_bytes())
            except (InvalidSignature, OSError) as exc:
                sys.exit(f"bad signature: {str(exc) or 'does not match'}")
            print("good signature")
        elif args.command == "manager":
            version = make_manager_release(args.out, args.key, args.manager, args.launcher)
            print(f"manager {version} release in {args.out}")
        else:
            release = check_release(args.folder, args.public)
            print(f"good manager {release['version']} release")
    except ReleaseError as exc:
        sys.exit(str(exc))


if __name__ == "__main__":
    main()
