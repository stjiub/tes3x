"""Resolve machine-local Xbox and xemu targets.

Targets describe machines; profiles describe builds.  The old [deploy] table is exposed as an
implicit target named ``xbox`` so existing local configurations keep working.
"""

from pathlib import PurePosixPath

from tes3x_paths import xbox_root


XBOX_DEFAULTS = {"port": 21, "user": "xbox", "password": "xbox", "ram": 64}
XEMU_DEFAULTS = {"ram": 64}
TARGET_KINDS = {"xbox", "xemu"}


class TargetError(ValueError):
    pass


def _split_remote(remote):
    remote = xbox_root(remote).rstrip("/")
    head, separator, tail = remote.rpartition("/")
    if not separator or not tail:
        raise TargetError("deploy.remote_root must name a game folder below a games root")
    return head, tail


def targets(local):
    """Return configured targets, including the legacy [deploy] compatibility target."""
    result = {}
    for name, raw in local.get("targets", {}).items():
        value = dict(raw)
        value["name"] = name
        result[name] = value

    legacy = local.get("deploy", {})
    if legacy and "xbox" not in result:
        value = dict(legacy)
        value.update({"name": "xbox", "kind": "xbox", "legacy": True})
        if legacy.get("remote_root"):
            value["games_root"], value["legacy_install_dir"] = _split_remote(
                legacy["remote_root"])
        result["xbox"] = value
    return result


def default_name(local, kind=None):
    available = targets(local)
    named = local.get("default_target")
    if named in available and (kind is None or available[named].get("kind") == kind):
        return named
    return next((name for name, target in available.items()
                 if kind is None or target.get("kind") == kind), None)


def rig_name(local):
    names = [name for name, target in targets(local).items()
             if target.get("kind") == "xbox" and target.get("rig")]
    if len(names) > 1:
        raise TargetError("only one Xbox target may have rig = true")
    return names[0] if names else None


def resolve(local, name=None, kind=None, required=False):
    """Resolve NAME (or default_target) and apply kind defaults."""
    available = targets(local)
    selected = name or default_name(local, kind)
    if selected is None:
        if required:
            label = f"{kind} " if kind else ""
            raise TargetError(f"no {label}target is configured")
        return None
    if selected not in available:
        raise TargetError(f"unknown target {selected!r}; choose from {', '.join(available) or 'none'}")
    target = dict(available[selected])
    if kind and target.get("kind") != kind:
        raise TargetError(f"target {selected!r} is {target.get('kind')!r}, not {kind!r}")
    defaults = XBOX_DEFAULTS if target.get("kind") == "xbox" else XEMU_DEFAULTS
    for key, value in defaults.items():
        target.setdefault(key, value)
    return target


def install_dir(profile, target=None):
    identity = profile.get("profile", {})
    value = identity.get("install_dir")
    if not value and identity.get("remote_root"):
        value = PurePosixPath(identity["remote_root"].replace("\\", "/").rstrip("/")).name
    if not value and target:
        value = target.get("legacy_install_dir")
    value = value or identity.get("name")
    if not value or value in {".", ".."} or "/" in value or "\\" in value or ":" in value:
        raise TargetError("profile.install_dir must be one folder name")
    return value


def remote_root(profile, target):
    if not target or target.get("kind") != "xbox":
        return None
    games = target.get("games_root")
    if not games:
        raise TargetError(f"target {target['name']!r} needs games_root")
    return xbox_root(games).rstrip("/") + "/" + install_dir(profile, target)


def path_check_root(local, profile, name=None):
    """Destination with the longest configured Xbox root, or the selected target's root."""
    if name:
        chosen = resolve(local, name, required=True)
        return remote_root(profile, chosen) if chosen.get("kind") == "xbox" else None
    roots = []
    for target in targets(local).values():
        if target.get("kind") == "xbox" and target.get("games_root"):
            roots.append(remote_root(profile, target))
    return max(roots, key=lambda item: len(item) - 2) if roots else None
