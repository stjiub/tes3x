"""Validate FATX component and full-path limits; locate TES3X's own files and per-user
data."""
import os
import re
from pathlib import Path

DEFAULT_REMOTE_ROOT = 'F:/Games/Morrowind'
NAME_MAX = 42
PATH_MAX = 250


def xbox_root(root):
    root = root.replace('\\', '/')
    if not re.match(r'^[A-Za-z]:/', root):
        raise ValueError('Xbox root must be absolute, e.g. F:/Games/Morrowind')
    parts = root[3:].rstrip('/').split('/') if root[3:].rstrip('/') else []
    if any(not p or p in {'.', '..'} for p in parts):
        raise ValueError('Xbox root contains an empty, dot or parent component')
    return root[0].upper() + ':/' + '/'.join(parts)


def check_paths(relative_paths, root=DEFAULT_REMOTE_ROOT, prefix=''):
    root = xbox_root(root)
    prefix = prefix.replace('\\', '/')
    if prefix.startswith('/') or ':' in prefix or any(p in {'.', '..'} for p in prefix.split('/')):
        raise ValueError('prefix must be relative, without dot components')
    problems = []
    longest = root
    longest_length = len(root) - 2
    for rel in [''] + sorted(set(relative_paths)):
        rel = rel.replace('\\', '/')
        if rel.startswith('/') or ':' in rel or any(p in {'.', '..'} for p in rel.split('/')):
            problems.append({'path': rel, 'reason': 'expected a relative path without dot components'})
            continue
        tail = '/'.join(p for p in (prefix.rstrip('/'), rel) if p)
        full = root.rstrip('/') + '/' + tail if tail else root
        length = len(full) - 2
        if length > longest_length:
            longest, longest_length = full, length
        if length > PATH_MAX:
            problems.append({'path': full, 'reason': f'full path {length} > {PATH_MAX} (drive excluded)'})
        for part in (full[3:].split('/') if full[3:] else []):
            if not part:
                problems.append({'path': full, 'reason': 'empty path component'})
            elif len(part) > NAME_MAX:
                problems.append({'path': full, 'reason': f'component {part!r}: {len(part)} > {NAME_MAX}'})
            elif any(ord(c) < 32 for c in part):
                problems.append({'path': full, 'reason': f'control character in {part!r}'})
    # Root component failures recur for every descendant; show each path once.
    return {'remote_root': root, 'path_limit': PATH_MAX, 'name_limit': NAME_MAX,
            'longest_path': longest, 'longest_length': longest_length,
            'problems': problems}


def require_paths(relative_paths, root=DEFAULT_REMOTE_ROOT, prefix=''):
    report = check_paths(relative_paths, root, prefix)
    if report['problems']:
        details = '\n'.join(f"  {p['path']}: {p['reason']}" for p in report['problems'][:12])
        raise ValueError(f"Xbox path validation failed ({len(report['problems'])} issues):\n{details}")
    print(f"Xbox paths: {report['remote_root']}; longest {report['longest_length']}/{PATH_MAX}: "
          f"{report['longest_path']}")
    return report


def data_dir():
    """Per-user folder for what TES3X downloads or caches; TES3X_DATA overrides it."""
    if os.environ.get('TES3X_DATA'):
        return Path(os.environ['TES3X_DATA'])
    if os.name == 'nt':
        return Path(os.environ.get('LOCALAPPDATA') or Path.home() / 'AppData' / 'Local') / 'TES3X'
    return Path(os.environ.get('XDG_DATA_HOME') or Path.home() / '.local' / 'share') / 'tes3x'


# Where TES3X's own files come from, by kind. All three are the checkout today; installed as a
# package, resources come from the package and writable files from data_dir().
CHECKOUT = Path(__file__).resolve().parents[1]


def resource(*parts):
    """Read-only data a command needs wherever TES3X is installed: the registries, hooks,
    symbols, examples, assets, add-ons, game tests, the manager's XBEs and the release key."""
    return CHECKOUT.joinpath(*parts)


def checkout(*parts):
    """What only a source checkout has: docs, the tools launched by path, the sources that the
    maintainer's generators rewrite, and git."""
    return CHECKOUT.joinpath(*parts)


def writable(*parts):
    """What TES3X writes beside the checkout (build output, downloads, the local config), bound
    for data_dir()."""
    return CHECKOUT.joinpath(*parts)
