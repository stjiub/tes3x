"""Bounded TES3 record/subrecord reader (plugins and Xbox map companions)."""
import struct
from pathlib import Path


RECORD_HEADER = struct.Struct('<4sIII')


def raw_records(path):
    """Yield offset, tag, unknown, flags and body without changing any record fields."""
    with open(path, 'rb') as stream:
        length = Path(path).stat().st_size
        # Retail expansion dependency placeholders are deliberately just TES3.
        if length == 4 and stream.read(4) == b'TES3':
            return
        stream.seek(0)
        while stream.tell() < length:
            offset = stream.tell()
            header = stream.read(RECORD_HEADER.size)
            if len(header) != RECORD_HEADER.size:
                raise ValueError(f'{path}: truncated record header at {offset}')
            tag, size, unknown, flags = RECORD_HEADER.unpack(header)
            if size > length - stream.tell():
                raise ValueError(f'{path}: {tag!r} exceeds file at {offset}')
            yield offset, tag, unknown, flags, stream.read(size)


def records(path):
    for _offset, tag, _unknown, flags, data in raw_records(path):
        yield tag, flags, data


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
