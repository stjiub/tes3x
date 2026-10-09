"""Multiplayer ghost and character creation plugin."""

import os
import struct
from .proto import (ARRIVAL_CELL, CHARGEN_SOURCE, GHOSTS, GHOST_CELL, GHOST_PLUGIN, zstr)

def record(tag, subs, flags=0):
    body = b"".join(name + struct.pack("<I", len(value)) + value for name, value in subs)
    return tag + struct.pack("<III", len(body), 0, flags) + body


def chargen_script():
    """CHARGEN_SOURCE compiled as the Construction Set would: opcodes little-endian, names and
    strings behind a length byte, an if or else followed by the count of statements it skips."""
    def name(text):
        return bytes([len(text)]) + text.encode("latin-1")

    def op(code):
        return struct.pack("<H", code)

    def position_cell(x, y, z, angle, cell):
        return (op(0x010C) + name("player") + op(0x1005) + struct.pack("<4f", x, y, z, angle)
                + name(cell))

    def in_cell(cell):
        return name((b" X" + op(0x1112) + b" c" + name(cell) + b" == 1").decode("latin-1"))

    data = b"".join(op(c) for c in (0x10DE, 0x1140, 0x10E3, 0x114C, 0x115A, 0x115D))
    data += op(0x0106) + b"\x01" + in_cell(ARRIVAL_CELL)
    data += position_cell(0, 0, 64, 0, ARRIVAL_CELL)
    data += op(0x0108) + b"\x02" + in_cell("Imperial Prison Ship")
    data += position_cell(61, -135, 24, 340, "Imperial Prison Ship")
    data += op(0x1124) + name("Bitter Coast Region") + struct.pack("<h", 1)
    data += op(0x0109)
    data += op(0x0105) + b"G" + name("CharGenState") + name(" 10")
    data += op(0x101C) + name("CharGen") + op(0x0101)
    head = b"CharGen".ljust(32, b"\0") + struct.pack("<5I", 0, 0, 0, len(data), 0)
    return record(b"SCPT", [(b"SCHD", head), (b"SCDT", data),
                            (b"SCTX", CHARGEN_SOURCE.encode("latin-1"))])


def ghost_plugin(master_size, master="Morrowind.esm"):
    """The plugin tes3xnet.c moves: one persistent NPC per peer slot, parked in a cell of its own.
    They have no AI packages and zero fight, flee, alarm and hello, so they stand where put.
    Talking to a ghost would turn it to face the speaker, away from where its player faces: the
    payload refuses the player's activation of one, and Morrowind.esm's noPickUp script swallows
    the rest where the ghost has its script variables."""
    hedr = (struct.pack("<fI", 1.3, 0) + b"TES3X".ljust(32, b"\0")
            + b"Other players, placed by the multiplayer patch.".ljust(256, b"\0")
            + struct.pack("<I", GHOSTS + 3))
    out = [record(b"TES3", [(b"HEDR", hedr), (b"MAST", zstr(master)),
                            (b"DATA", struct.pack("<Q", master_size))])]
    items = ("common_shirt_01", "common_pants_01", "common_shoes_01")
    for i in range(1, GHOSTS + 1):
        subs = [(b"NAME", zstr(f"tes3x_ghost{i}")), (b"FNAM", zstr(f"Player {i}")),
                (b"RNAM", zstr("Dark Elf")), (b"CNAM", zstr("Commoner")), (b"ANAM", b"\0"),
                (b"BNAM", zstr("b_n_dark elf_m_head_01")),
                (b"KNAM", zstr("b_n_dark elf_m_hair_01")), (b"SCRI", zstr("noPickUp")),
                (b"NPDT", struct.pack("<hBBB3xI", 1, 50, 0, 0, 0)),
                (b"FLAG", struct.pack("<I", 0x1A))]  # essential, autocalc
        subs += [(b"NPCO", struct.pack("<i32s", 1, item.encode())) for item in items]
        subs.append((b"AIDT", bytes(12)))
        out.append(record(b"NPC_", subs, flags=0x400))  # references persist
    out.append(chargen_script())
    cell = [(b"NAME", zstr(GHOST_CELL)), (b"DATA", struct.pack("<Iii", 1, 0, 0)),
            (b"WHGT", struct.pack("<f", 0)), (b"AMBI", struct.pack("<3If", 0x404040, 0, 0, 0))]
    for i in range(1, GHOSTS + 1):
        cell += [(b"FRMR", struct.pack("<I", i)), (b"NAME", zstr(f"tes3x_ghost{i}")),
                 (b"DATA", struct.pack("<6f", 128.0 * i, 0, 0, 0, 0, 0))]
    out.append(record(b"CELL", cell))
    # A new character stands here, out of the shared world, while choosing race, class and the
    # rest; the floor keeps it from falling the whole time.
    out.append(record(b"CELL", [
        (b"NAME", zstr(ARRIVAL_CELL)), (b"DATA", struct.pack("<Iii", 1, 0, 0)),
        (b"WHGT", struct.pack("<f", 0)), (b"AMBI", struct.pack("<3If", 0x808080, 0x808080, 0, 0)),
        (b"FRMR", struct.pack("<I", GHOSTS + 1)), (b"NAME", zstr("In_Lava_Blacksquare")),
        (b"DATA", struct.pack("<6f", 0, 0, 0, 0, 0, 0))]))
    return b"".join(out)


def write_ghost_plugin(data_files, master):
    """Write the ghost plugin into a staged Data Files; the engine loads every plugin there."""
    target = os.path.join(data_files, GHOST_PLUGIN)
    with open(target, "wb") as f:
        f.write(ghost_plugin(os.path.getsize(master)))
    return target
