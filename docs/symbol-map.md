# Symbol map

`symbols/curated.json` names functions and code sites in the retail Xbox `morrowind.xbe`, so that
an address in a crash log, a profile or a disassembly means something. `tools/tes3x_sym.py` builds
the analysis the names hang on and answers questions about it; the profiler and census renderers
use it to label what they print.

The Xbox and PC builds of Morrowind come from the same source, compiled differently. Most of the
map is made by pairing Xbox functions with their PC counterparts, whose names the PC modding
community has already worked out.

## Records

```json
{
  "va": "0x00116C30",
  "name": "Land::GetVertexPosition",
  "kind": "function",
  "confidence": "matched",
  "provenance": "call-graph match score 1.00; returns z=-2048 when the land has no geometry",
  "pc_va": "0x004CA9E0",
  "note": "vertex buffer base is [[[lod+0x98]+0x30]+0x14]+4 | 0x80000000, the D3D resource Data field"
}
```

| field | meaning |
|---|---|
| `va` | the address in the retail XBE |
| `name` | `Class::Method` where known; a trailing `?` marks a candidate |
| `kind` | `function` (the default), `site` for one instruction inside a function, or `data` for a global |
| `confidence` | how far to trust the name (below) |
| `provenance` | where it came from; `mwse:` names come from MWSE's address list |
| `pc_va` | the matching address in PC `Morrowind.exe` 1.6.1820, when there is one |
| `note` | optional: behaviour worth knowing before touching it |

`confidence` is one of:

- `verified`: observed at runtime, or relied on by a patch that works.
- `matched`: carried across from the PC build by the matching below. Usually right, sometimes
  wrong where the two compilers inlined differently.
- `guess`: a reading of the code, not yet tested.

Treat only `verified` as fact. Most records are `matched` names from MWSE.

## Using it

The tool needs [capstone](https://www.capstone-engine.org/) (`python -m pip install capstone`) and
the binaries it analyses, which you supply: your retail `morrowind.xbe` and, for matching, the PC
`Morrowind.exe` 1.6.1820 without Code Patch changes (a Steam install with Code Patch keeps it as
`Morrowind.Original.exe`). The analysis goes to `build/symbols.db` under the working folder; it is
rebuilt from the binaries, never edited and never committed.

```
python tools/tes3x_sym.py build morrowind.xbe --tag xbe       # function inventory
python tools/tes3x_sym.py build Morrowind.exe --tag pc
python tools/tes3x_sym.py match                               # pair by shared strings
python tools/tes3x_sym.py propagate                           # extend through the call graph
python tools/tes3x_sym.py seed morrowind.xbe                  # addresses the patcher finds
python tools/tes3x_sym.py names path/to/MWSE                  # MWSE names onto matched functions
```

Then query it:

```
python tools/tes3x_sym.py lookup 0x00098430                   # the function holding an address
python tools/tes3x_sym.py disasm 0x0013D370 --len 0x40        # disassembly with names
python tools/tes3x_sym.py callers 0x0013D370                  # or --callees
python tools/tes3x_sym.py stats
```

The queries need the database; `build` of the XBE alone is enough for `lookup`, `disasm` and
`callers`, and the PC build and matching add the PC counterpart. The renderers below name call sites
only when `build/symbols.db` exists, and print bare addresses otherwise.

## Adding a name

```
python tools/tes3x_sym.py annotate 0x00193710 Ini::ReadInt --confidence verified \
    --provenance "traced: 4 args cdecl (section, key, default, file), returns int"
```

Record `verified` only for what a run or a working patch showed, and say how in `provenance`.

## How the matching works

1. **Inventory.** Recursive descent from the entry point and every call target, plus a sweep for
   function prologues, finds about 26,000 functions in the XBE's `.text`. Middleware sections
   (D3D, DirectSound, Bink and the like) are left out.
2. **String anchoring.** About 85% of the XBE's longer strings also appear in the PC build. A
   string used by exactly one function on each side pairs those two functions.
3. **Propagation.** Matched functions that call the same number of functions in the same order
   pair their callees, repeated until nothing new pairs.
4. **Names.** MWSE's addresses for `Morrowind.exe` 1.6.1820 name the PC side; each matched Xbox
   function inherits the name as `matched`.

About 13% of the XBE's functions match. Most of the rest are never the target of a direct call:
about 19,700 of the 26,000 are reached only through a C++ virtual table, which the call-graph step
cannot follow. The same limit applies to call-site hooks such as the profiler: a virtually
dispatched function has no call site to redirect. The Xbox-only code (the XDK, FATX, the cache
partitions and the controller) has no PC counterpart at all, so it is named only as it is traced.

## Where the numbers go

- `tools/tes3x_prof.py targets NAME` turns a name into the address list for
  `--apply profile=`, and `report FILE` renders `E:\tes3xprof.bin`
  ([Profiling](diagnostics.md#profiling)).
- `tools/tes3x_heap.py` and `tools/tes3x_mem.py` render the heap and memory censuses, naming call
  sites from the map ([Heap census](diagnostics.md#heap-census)).

## Credit and limits

`mwse:` names come from [MWSE](https://github.com/MWSE/MWSE)'s address definitions (MIT licence),
carried across by the matching above. The map holds addresses and names only: no bytes of the game,
no decompiled code.
