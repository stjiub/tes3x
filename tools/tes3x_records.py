"""Bounded TES3 record/subrecord reader (plugins and Xbox map companions)."""
import struct
from pathlib import Path


def records(path):
    with open(path, 'rb') as stream:
        length = Path(path).stat().st_size
        # Retail expansion dependency placeholders are deliberately just TES3.
        if length == 4 and stream.read(4) == b'TES3':
            return
        stream.seek(0)
        while stream.tell() < length:
            offset = stream.tell()
            header = stream.read(16)
            if len(header) != 16:
                raise ValueError(f'{path}: truncated record header at {offset}')
            tag, size, _, flags = struct.unpack('<4sIII', header)
            if size > length - stream.tell():
                raise ValueError(f'{path}: {tag!r} exceeds file at {offset}')
            yield tag, flags, stream.read(size)


def subrecords(data):
    offset = 0
    while offset < len(data):
        if offset + 8 > len(data):
            raise ValueError(f'truncated subrecord header at {offset}')
        tag, size = struct.unpack_from('<4sI', data, offset)
        offset += 8
        if size > len(data) - offset:
            raise ValueError(f'{tag!r}: subrecord exceeds record at {offset}')
        yield tag, data[offset:offset + size]
        offset += size
