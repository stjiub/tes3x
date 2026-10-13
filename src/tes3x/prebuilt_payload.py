"""Select a versioned, checked payload for an exact source set."""

import hashlib
import json

from tes3x.paths import resource


def select_prebuilt(sources, flags=""):
    """Return (name, PE, manifest) when no compile-time options differ."""
    if flags.strip():
        return None
    root = resource("payloads")
    index = json.loads((root / "index.json").read_text(encoding="utf-8"))
    if index.get("schema") != 1:
        raise ValueError("unsupported prebuilt payload index")
    for variant in index["variants"]:
        if list(sources) != variant["sources"]:
            continue
        name = variant["name"]
        pe, manifest = root / f"{name}.pe", root / f"{name}.json"
        for path, key in ((pe, "pe_sha256"), (manifest, "manifest_sha256")):
            actual = hashlib.sha256(path.read_bytes()).hexdigest()
            if actual != variant[key]:
                raise ValueError(f"prebuilt payload {path.name}: SHA-256 mismatch")
        return name, pe, manifest
    return None
