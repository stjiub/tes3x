"""Inspect Xbox TES3 map companions and optionally decode a CMAP tile."""
import argparse
import io
import json
import struct
from collections import Counter
from pathlib import Path

from tes3x_records import records, subrecords


def inspect(path, preview=None):
    tags, sizes = Counter(), Counter()
    headers = Counter()
    examples = []
    decoded = False
    world = None
    for tag, _, data in records(path):
        tags[tag.decode('ascii')] += 1
        for sub, value in subrecords(data):
            sizes[f'{tag.decode()}/{sub.decode()}:{len(value)}'] += 1
            if tag == b'CMAP' and sub == b'HEDR':
                if len(value) != 84:
                    raise ValueError('unexpected CMAP HEDR size')
                name = value[1:65].split(b'\0')[0].decode('cp1252')
                headers[value[0]] += 1
                if len(examples) < 5:
                    examples.append({'flag': value[0], 'name': name,
                                     'tail_hex': value[65:].hex()})
            if tag == b'CMAP' and sub == b'DATA' and preview and not decoded:
                if len(value) != 32768:
                    raise ValueError('unexpected CMAP DATA size')
                from PIL import Image
                from tes3x_convert import dds_header
                surface = dds_header(256, 256, 'DXT1', 1, len(value)) + value
                Image.open(io.BytesIO(surface)).save(preview)
                decoded = True
            if tag == b'XMAP' and sub == b'MAPH':
                world = list(struct.unpack('<II', value))
    return {'bytes': Path(path).stat().st_size, 'records': dict(tags),
            'subrecord_sizes': dict(sorted(sizes.items())), 'cmap_flags': dict(headers),
            'header_examples': examples, 'xmap_maph_u32': world}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('map')
    parser.add_argument('--json')
    parser.add_argument('--preview', help='PNG output for the first local-map tile')
    args = parser.parse_args()
    result = json.dumps(inspect(args.map, args.preview), indent=2)
    if args.json:
        Path(args.json).write_text(result, encoding='utf-8')
    print(result)
