"""Conservatively prune unreachable mod assets from TES3/Xbox builds."""
import fnmatch
import os
import re
import struct
from collections import defaultdict, deque
from pathlib import Path, PurePosixPath

from tes3x_bsa import Bsa
from tes3x_records import records, subrecords

ASSET_DIRS = {'meshes', 'textures', 'icons', 'sound', 'bookart'}
IMAGE_EXT = {'.dds', '.tga', '.bmp'}
PATH_TEXT = re.compile(r'[^\x00-\x1f"<>|]+?\.(?:nif|kf|dds|tga|bmp|wav|mp3)', re.I)
QUOTED_PATH = re.compile(r'"([^"\r\n]+\.(?:nif|kf|dds|tga|bmp|wav|mp3))"', re.I)
BOOK_IMAGE = re.compile(r'<img\b[^>]*\bsrc\s*=\s*["\']?([^"\'<>]+?)(?:["\']|\s+(?:width|height)\s*=|\s*>)', re.I)


def asset_path(value, directory):
    value = value.rstrip('\0').strip().replace('\\', '/').lower()
    # Some authoring tools leave absolute editor paths in NIF textures.
    parts = value.split('/')
    if directory in parts:
        value = '/'.join(parts[parts.index(directory):])
    elif value:
        value = directory + '/' + value
    if not value or ':' in value or '..' in value.split('/'):
        return None
    return str(PurePosixPath(value))


def plugin_paths(path):
    for record, _, data in records(path):
        for tag, value in subrecords(data):
            directory = None
            if tag == b'MODL':
                directory = 'meshes'
            elif tag == b'ITEX':
                directory = 'icons'
            elif (record, tag) in {(b'LTEX', b'DATA'), (b'MGEF', b'PTEX'), (b'BSGN', b'TNAM')}:
                directory = 'textures'
            elif (record, tag) == (b'SOUN', b'FNAM'):
                directory = 'sound'
            if directory:
                result = asset_path(value.decode('cp1252', errors='replace'), directory)
                if result:
                    yield result
            if record == b'BOOK' and tag == b'TEXT':
                for match in BOOK_IMAGE.finditer(value.decode('cp1252', errors='replace')):
                    result = asset_path(match[1], 'bookart')
                    if result:
                        yield result
            # Script source and compiled string operands (including dialogue Say).
            if tag in {b'SCTX', b'SCDT', b'BNAM'} and record in {b'SCPT', b'INFO'}:
                text = value.decode('cp1252', errors='replace')
                raw_paths = [m[1] for m in QUOTED_PATH.finditer(text)]
                # Include printable compiled operands conservatively. Extra
                # candidates are reported, never used to remove a quoted path.
                raw_paths += [m[0] for m in PATH_TEXT.finditer(text)]
                for raw in raw_paths:
                    raw = raw.strip()
                    ext = PurePosixPath(raw).suffix.lower()
                    directory = 'sound' if ext in {'.wav', '.mp3'} else 'meshes' if ext in {'.nif', '.kf'} else 'textures'
                    result = asset_path(raw, directory)
                    if result:
                        yield result


def nif_textures(data):
    """Read external NiSourceTexture fields from NIF 4.0.0.2."""
    header = b'NetImmerse File Format, Version 4.0.0.2\n'
    if not data.startswith(header) or len(data) < len(header) + 8:
        raise ValueError('unsupported or truncated NIF header (expected 4.0.0.2)')
    version, blocks = struct.unpack_from('<II', data, len(header))
    if version != 0x04000002 or blocks == 0 or blocks > len(data) // 4:
        raise ValueError('invalid NIF version/block count')
    marker = struct.pack('<I', 15) + b'NiSourceTexture'
    offset = 0
    while (offset := data.find(marker, offset)) >= 0:
        pos = offset + len(marker)
        offset = pos
        try:
            name_size = struct.unpack_from('<I', data, pos)[0]
            pos += 4 + name_size + 8  # NiObjectNET name, extra-data, controller
            external = data[pos]
            pos += 1
            if external == 0:
                continue
            if external != 1:
                raise ValueError('invalid external texture flag')
            size = struct.unpack_from('<I', data, pos)[0]
            pos += 4
            if size == 0 or size > 4096 or pos + size + 13 > len(data):
                raise ValueError('invalid external texture string bounds')
            name = data[pos:pos + size].decode('cp1252')
            if any(ord(c) < 32 for c in name):
                raise ValueError('control character in texture name')
            result = asset_path(name, 'textures')
            if not result:
                raise ValueError(f'invalid texture path {name!r}')
            yield result
        except (IndexError, struct.error, UnicodeError) as exc:
            raise ValueError('truncated NiSourceTexture') from exc


def prune(filemap, vanilla, keep=()):
    vanilla = Path(vanilla)
    base = Bsa(vanilla / 'Morrowind.bsa')
    master = vanilla / 'Morrowind.esm'
    if not master.is_file():
        raise ValueError('reachability requires the retail Morrowind.esm')
    reasons = defaultdict(set)
    queue = deque()
    visited = set()
    missing = set()
    warnings = []
    image_stems = defaultdict(list)
    for key in filemap:
        if PurePosixPath(key).suffix in IMAGE_EXT:
            image_stems[str(PurePosixPath(key).with_suffix(''))].append(key)

    def root(key, reason):
        reasons[key].add(reason)
        queue.append(key)

    for key in sorted(filemap):
        top = key.split('/')[0]
        if top not in ASSET_DIRS:
            root(key, 'non-prunable / globbed')
        elif (base.contains(key) or (PurePosixPath(key).suffix in IMAGE_EXT and any(
                base.contains(str(PurePosixPath(key).with_suffix(ext))) for ext in IMAGE_EXT))):
            root(key, 'vanilla archive replacement')
        elif key.startswith('sound/vo/'):
            root(key, 'voice selection by engine')
        if any(fnmatch.fnmatchcase(key, pattern.lower().replace('\\', '/')) for pattern in keep):
            root(key, 'explicit keep')

    plugins = [master] + [Path(src) for key, (_, src) in sorted(filemap.items())
                          if key.endswith(('.esm', '.esp'))]
    for plugin in plugins:
        for key in plugin_paths(plugin):
            root(key, f'plugin:{plugin.name}')

    while queue:
        key = queue.popleft()
        if key in visited:
            continue
        visited.add(key)
        ext = PurePosixPath(key).suffix
        if ext == '.nif':
            p = PurePosixPath(key)
            for companion in (p.with_suffix('.kf'), p.with_name('x' + p.name), p.with_name('x' + p.stem + '.kf')):
                if str(companion) in filemap:
                    root(str(companion), f'animation companion:{key}')
        if ext in IMAGE_EXT:
            # Keep all supported extension alternatives; retail probing order
            # is not established. A .tga reference commonly resolves to .dds.
            for alias in image_stems.get(str(PurePosixPath(key).with_suffix('')), ()):
                if alias != key:
                    root(alias, f'texture alternative:{key}')
        if key not in filemap:
            alternatives = [str(PurePosixPath(key).with_suffix(e)) for e in IMAGE_EXT] if ext in IMAGE_EXT else []
            if not base.contains(key) and not any(a in filemap or base.contains(a) for a in alternatives):
                missing.add(key)
            continue
        if ext == '.nif':
            source = Path(filemap[key][1])
            try:
                for texture in nif_textures(source.read_bytes()):
                    root(texture, f'mesh:{key}')
            except ValueError as exc:
                # Unknown layouts cannot prove textures unreachable.
                warnings.append(f'{key}: {exc}; preserving all textures')
                for texture in filemap:
                    if texture.startswith('textures/'):
                        root(texture, 'unsupported mesh safety fallback')

    kept = {key: value for key, value in filemap.items() if key in visited}
    removed = sorted(set(filemap) - set(kept))
    report = {
        'input_files': len(filemap), 'kept_files': len(kept),
        'removed_files': len(removed),
        'removed_bytes': sum(os.path.getsize(filemap[k][1]) for k in removed),
        'removed': removed, 'missing_references': sorted(missing),
        'warnings': warnings,
        'reasons': {k: sorted(reasons[k]) for k in sorted(kept)},
    }
    return kept, report
