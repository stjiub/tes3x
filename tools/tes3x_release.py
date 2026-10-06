#!/usr/bin/env python3
"""The project's release signing key (Ed25519), which the console manager checks updates against.

    tes3x_release.py keygen KEY            write a new private key; prints its public key
    tes3x_release.py public KEY            print the public key of a private key
    tes3x_release.py sign KEY FILE         write FILE.sig (64 bytes)
    tes3x_release.py verify PUBLIC FILE    check FILE.sig against a public key (hex or file)

The private key never goes into a repository. The public key the manager embeds is
keys/release.pub.
"""

import argparse
import hashlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PUBLIC = ROOT / "keys" / "release.pub"


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


def load_private(path):
    _, serialization = _ed25519()
    return serialization.load_pem_private_key(Path(path).read_bytes(), password=None)


def load_public(text):
    ed25519, _ = _ed25519()
    path = Path(text)
    value = path.read_text(encoding="ascii").split()[0] if path.is_file() else text
    return ed25519.Ed25519PublicKey.from_public_bytes(bytes.fromhex(value))


def keygen(path):
    ed25519, serialization = _ed25519()
    path = Path(path)
    if path.exists():
        raise SystemExit(f"{path} exists; a release key is never replaced by accident")
    private = ed25519.Ed25519PrivateKey.generate()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "xb") as out:
        out.write(private.private_bytes(serialization.Encoding.PEM,
                                        serialization.PrivateFormat.PKCS8,
                                        serialization.NoEncryption()))
    return public_bytes(private)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="command", required=True)
    sub.add_parser("keygen").add_argument("key")
    sub.add_parser("public").add_argument("key")
    p = sub.add_parser("sign")
    p.add_argument("key")
    p.add_argument("file")
    p = sub.add_parser("verify")
    p.add_argument("public", nargs="?", default=str(PUBLIC))
    p.add_argument("file")
    args = ap.parse_args()

    if args.command in ("keygen", "public"):
        public = keygen(args.key) if args.command == "keygen" else public_bytes(
            load_private(args.key))
        print(f"{public.hex()}  fingerprint {fingerprint(public)}")
    elif args.command == "sign":
        signature = load_private(args.key).sign(Path(args.file).read_bytes())
        Path(args.file + ".sig").write_bytes(signature)
        print(f"wrote {args.file}.sig")
    else:
        from cryptography.exceptions import InvalidSignature
        try:
            load_public(args.public).verify(Path(args.file + ".sig").read_bytes(),
                                            Path(args.file).read_bytes())
        except (InvalidSignature, OSError) as exc:
            sys.exit(f"bad signature: {str(exc) or 'does not match'}")
        print("good signature")


if __name__ == "__main__":
    main()
