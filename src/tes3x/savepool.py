"""Save pools: a build whose certificate carries its own title ID saves to its own
E:/UDATA/<id> folder, apart from every other build.

The engine faults if it has to create that folder itself, while copying its title image, so
the folder's TitleMeta.xbx and TitleImage.xbx must be in place before a pooled build first runs.
tes3xpool.txt beside them names the pool, which tells a TES3X pool from another title's folder.
"""

import zlib

from tes3x.inject import Xbe

SHARED_ID = 0x42530005
# Licensed titles begin with two capital letters; a digit keeps pools clear of them.
PREFIX = 0x5433
MARKER = "tes3xpool.txt"


def pool_id(name, explicit=None):
    """The pool's title ID: an explicit one if given, otherwise one derived from its name."""
    if explicit:
        value = int(str(explicit), 16)
        if not 0 < value <= 0xFFFFFFFF or value == SHARED_ID:
            raise ValueError(f"save pool id {explicit!r} is not a usable title ID")
        return value
    low = zlib.crc32(name.strip().casefold().encode("utf-8")) & 0xFFFF
    return PREFIX << 16 | (low or 1)


def folder(value):
    return f"UDATA/{value:08X}"


def title_image(xbe_path):
    x = Xbe(open(xbe_path, "rb").read())
    for s in x.sections:
        if s.name == "$$XTIMAGE":
            return bytes(x.data[s.raw:s.raw + s.rsize])
    raise ValueError(f"{xbe_path} has no title image section")


def files(name, image):
    """{name: bytes} for the pool folder."""
    meta = "\ufeffTitleName=Morrowind: %s\r\n" % name
    return {"TitleMeta.xbx": meta.encode("utf-16-le"), "TitleImage.xbx": image,
            MARKER: name.encode("utf-8")}
