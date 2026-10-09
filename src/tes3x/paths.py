"""Validate FATX component and full-path limits; locate TES3X's own files and per-user
data."""
import os
import re
import sys
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


# Where TES3X's own files come from, by kind. A wheel carries the resources in _data (setup.py);
# a checkout, or an editable install of one, reads them where they are. What TES3X writes goes to
# data_dir() or where asked.
CHECKOUT = Path(__file__).resolve().parents[2]
PACKAGED = Path(__file__).resolve().parent / '_data'


def portable_folder(prefix=sys.prefix):
    """The portable folder whose python/ is running this, with TES3X.exe beside it; or None."""
    folder = Path(prefix).resolve().parent
    return folder if (folder / 'TES3X.exe').is_file() else None


PORTABLE = portable_folder()


def resource(*parts):
    """Read-only data a command needs wherever TES3X is installed: the registries, hooks,
    symbols, examples, assets, add-ons, game tests, the manager's header and the release key."""
    return (PACKAGED if PACKAGED.is_dir() else CHECKOUT).joinpath(*parts)


def bundled(*parts):
    """What a portable folder carries beside the package: externals/ (7-Zip and LLVM), the
    manager's XBEs and VERSION. Elsewhere the checkout's, which has them only once built there;
    otherwise the tools fall back to PATH or a build."""
    return (PORTABLE or CHECKOUT).joinpath(*parts)


def checkout(*parts):
    """What only a source checkout has: docs, the tools launched by path, the sources that the
    maintainer's generators rewrite, git, and the maintainer's build output (build/)."""
    return CHECKOUT.joinpath(*parts)


# The documentation, for messages and help: an installed TES3X has no docs/ folder.
DOCS_URL = 'https://github.com/stjiub/tes3x/blob/main/docs/'


def docs_url(page):
    return DOCS_URL + page


CONFIG_HELP = f"local config (default: see {docs_url('configuration.md#local-config')})"


CONFIG_NAME = 'tes3x.local.toml'


def local_config(given=None):
    """The local config: given, else TES3X_CONFIG, else tes3x.local.toml in the working
    directory, else a checkout's, else the per-user one in data_dir(), which may not exist yet."""
    if given:
        return Path(given)
    if os.environ.get('TES3X_CONFIG'):
        return Path(os.environ['TES3X_CONFIG'])
    for path in (Path.cwd() / CONFIG_NAME, CHECKOUT / CONFIG_NAME):
        if path.is_file():
            return path
    return data_dir() / CONFIG_NAME
