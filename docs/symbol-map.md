# Symbol map

`symbols/curated.json` names functions and code sites in the retail Xbox `morrowind.xbe`, so that
an address in a crash log, a profile or a disassembly means something. `tes3x sym` builds
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
| `note` | optional: behaviour worth knowing before touching it; shown above the function in `decompile` |
| `type` | optional, for `data`: the global's C type, such as `TES3::WorldController*` |
| `signature` | optional, for a function: its C prototype without `this`, such as `float __thiscall getSkill(int id)` |

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
tes3x sym build morrowind.xbe --tag xbe       # function inventory
tes3x sym build Morrowind.exe --tag pc
tes3x sym match                               # pair by shared strings
tes3x sym propagate                           # extend through the call graph
tes3x sym body-match                          # exact bodies and references in Ghidra
tes3x sym propagate                           # extend from the body matches
tes3x sym seed morrowind.xbe                  # addresses the patcher finds
tes3x sym names path/to/MWSE                  # MWSE names onto matched functions
```

Then query it:

```
tes3x sym lookup 0x00098430                   # the function holding an address
tes3x sym disasm 0x0013D370 --len 0x40        # disassembly with names
tes3x sym callers 0x0013D370                  # or --callees
tes3x sym stats
```

The queries need the database; `build` of the XBE alone is enough for `lookup`, `disasm` and
`callers`, and the PC build and matching add the PC counterpart. The renderers below name call sites
only when `build/symbols.db` exists, and print bare addresses otherwise.

## Decompiling

`decompile` and `refs` use [Ghidra](https://ghidra-sre.org/) 11.3 or later with the
[ghidra-xbe](https://github.com/XboxDev/ghidra-xbe) loader, found through `GHIDRA_INSTALL_DIR` or
`[paths] ghidra` in `tes3x.local.toml` (and a JDK Ghidra accepts). Import and analyse the images
once; it takes several minutes per image:

```
tes3x sym ghidra-setup                        # both images in symbols.db
tes3x sym decompile 0x00111920                # pseudo-C of the function
tes3x sym decompile 0x00111920 --pc           # and its PC counterpart
tes3x sym refs 0x003CB5F4                     # code and data references
tes3x sym ghidra-sync                         # apply now, list rejections
tes3x sym ghidra-stop
```

The first request starts a headless Ghidra in the background that keeps the project open; later
requests take well under a second, and the process exits after 30 idle minutes. Before a request,
whenever `curated.json`, `structs.json` or the generated layouts have changed, Ghidra is given:

- the struct and enum types below;
- every name, with `Class::method` placed in a class whose struct exists, so that `this` takes
  that type. A method without a recorded signature is made `__thiscall` when its first
  instructions read `ECX`;
- each `signature`, each `note` as a comment above the function, and each global's `type`.

`body-match` also uses the analysed programs. It runs Ghidra's exact-instruction and
exact-mnemonic correlators, then seeds the function-reference correlator with the established
pairs. Exact matches must be unique on both sides. Reference matches must be mutually best, at
least 0.95 similar and have confidence 250 or greater; those thresholds had no errors in the
established-pair calibration set. Conflicts are reported and left unchanged.

PC functions take the names, signatures and notes of their matched Xbox functions. Nothing is
saved into the Ghidra project, which lives in `build/ghidra/` and is never committed: what a
session learns goes into the files above.

## Types

MWSE describes the PC engine's structs in C++ headers. `tes3x layouts` compiles them for 32-bit
MSVC with libclang and writes the exact layouts to `build/ghidra/types-mwse.json`; it needs
`python -m pip install libclang`, clang (`[paths] llvm`) and the MSVC and Windows SDK headers of a
Visual Studio C++ install. MWSE asserts its own sizes and offsets, and the tool reports any that
fail.

```
tes3x layouts path/to/MWSE
```

The Xbox build shares most layouts with the PC, not all. `symbols/structs.json` corrects them for
the Xbox image only:

```json
{
  "name": "TES3::WorldController",
  "pc_valid_until": "0x6C",
  "note": "4 bytes shorter than the PC somewhere in 0x6C..0xA4",
  "fields": [
    {"offset": "0xBC", "name": "gvarCharGenState", "type": "TES3::GlobalVariable*",
     "confidence": "verified", "provenance": "transition-autosaves gates saving on it"}
  ]
}
```

`pc_valid_until` drops the PC fields from that offset on; each field replaces whatever PC field it
overlaps; a struct the PC does not have takes a `size`. Types are C++ names (unqualified ones are
looked up in `TES3` and then `NI`) with `*` and `[N]`.

## Adding a name

```
tes3x sym annotate 0x00193710 Ini::ReadInt --confidence verified \
    --provenance "traced: 4 args cdecl (section, key, default, file), returns int"
tes3x sym annotate 0x003CB5F4 WorldController::instance --kind data \
    --type "TES3::WorldController*" --confidence verified --provenance "..."
tes3x sym annotate 0x0017C3D0 MobileActor::applyHealthDamage \
    --signature "bool __thiscall applyHealthDamage(float damage, bool isPlayerAttack, bool scaleWithDifficulty, bool doNotChangeHealth)"
```

Record `verified` only for what a run or a working patch showed, and say how in `provenance`.
`annotate` keeps fields it is not given; a signature names the arguments every caller's pseudo-C
then shows. Struct fields go into `structs.json` by hand.

## How the matching works

1. **Inventory.** Recursive descent from the entry point and every call target, plus a sweep for
   function prologues, finds about 26,000 functions in the XBE's `.text`. Middleware sections
   (D3D, DirectSound, Bink and the like) are left out.
2. **String anchoring.** About 85% of the XBE's longer strings also appear in the PC build. A
   string used by exactly one function on each side pairs those two functions.
3. **Pointer tables.** Already-paired slots align virtual tables. A long function-address table
   present in both images is aligned as a sequence; only equal-length gaps between anchors pair.
4. **Propagation.** Matched functions align callees and unique callers, repeated until nothing
   new pairs.
5. **Bodies and references.** Ghidra pairs unique exact instruction or mnemonic sequences, then
   high-confidence mutually best function-reference candidates. Propagation runs again afterward.
6. **Names.** MWSE's addresses for `Morrowind.exe` 1.6.1820 name the PC side; each matched Xbox
   function inherits the name as `matched`.

About 16% of the XBE's functions match. Most of the rest are never the target of a direct call:
about 19,700 of the 26,000 are reached only through a C++ virtual table, which the call-graph step
cannot follow. The same limit applies to call-site hooks such as the profiler: a virtually
dispatched function has no call site to redirect. The Xbox-only code (the XDK, FATX, the cache
partitions and the controller) has no PC counterpart at all, so it is named only as it is traced.

## Where the numbers go

- `tes3x prof targets NAME` turns a name into the address list for
  `--apply profile=`, and `report FILE` renders `E:\tes3xprof.bin`
  ([Profiling](diagnostics.md#profiling)).
- `tes3x heap` and `tes3x mem` render the heap and memory censuses, naming call
  sites from the map ([Heap census](diagnostics.md#heap-census)).

## Credit and limits

`mwse:` names come from [MWSE](https://github.com/MWSE/MWSE)'s address definitions (MIT licence),
carried across by the matching above, and so do the struct layouts `tes3x layouts` compiles from
its headers. The map holds addresses, names, types and signatures only: no bytes of the game, no
decompiled code.
