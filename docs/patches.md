# Patches

Generated from [`patches.toml`](../patches.toml) by `tools/tes3x_patches.py --write`;
edit that file, not this one. Fixes that are not implemented are in
[candidates.md](candidates.md). A linked name opens the patch's folder of notes and tests.

`release` patches are ready for general use; `development` patches are still being tested.
"By name" patches are only applied when a profile enables them.

| patch | what it does | from | category | channel | selected by |
|---|---|---|---|---|---|
| `payload=FILE.pe` | Inject a code section and run it from the entry point. | TES3X | infrastructure | development | build option |
| `boot-media` | Permit booting from any media and region, not just a retail DVD. | TES3X | infrastructure | development | every build |
| `drive-letters=LETTER` | Point every Data Files asset path at one drive. | TES3X | infrastructure | development | every build |
| `save-staging=LETTER` | Stage saves on one volume with UDATA so the commit renames instead of copying. | TES3X | infrastructure | development | build option |
| `title=NAME` | Rename the XBE's certificate title, the name a dashboard falls back to. | TES3X | infrastructure | development | build option |
| `multi-bsa` | Load every archive listed in tes3xarch.txt, not just Morrowind.bsa. | TES3X | infrastructure | development | delta-bsa packing |
| `script-ext` | Add script opcodes: widen the length bounds and hook Script::RunFunction. | TES3X | compat | development | by name |
| `mwse-legacy` | Interpret legacy MWSE 0.9.4 bytecode embedded in compiled scripts. | TES3X | compat | development | by name |
| [`mcp-1`](../patches/mcp-1/) | Stop an unresolvable reference being restamped as created at runtime. | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #1 | core | development | development preset |
| [`mcp-97`](../patches/mcp-97/) | Advance the script parser correctly while initializing saved data. | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #97 | core | development | development preset |
| [`mcp-154`](../patches/mcp-154/) | Pad compiled script-data allocations to keep dword reads in bounds. | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #154 | core | development | development preset |
| [`mcp-102`](../patches/mcp-102/) | Reactivate script-triggered objects after their script mod is removed. | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #102 | correctness | development | development preset |
| [`dxt5-size`](../patches/dxt5-size/) | Allocate DXT5 textures at their real size, not a negative one that corrupts video memory. | TES3X | core | development | development preset |
| [`video-arena`](../patches/video-arena/) | With more than 64 MB, size the texture and vertex buffer arena from [Xbox] VideoMemoryKB. | TES3X | compat | development | by name |
| [`rotating-autosaves`](../patches/rotating-autosaves/) | Rotate automatic saves through INI-configurable slots. | [OpenMW](https://openmw.org) | qol | development | by name |
| [`build-preferences`](../patches/build-preferences/) | Apply profile-selected player preferences after stored Xbox options load. | TES3X | qol | development | build option |
| [`transition-autosaves`](../patches/transition-autosaves/) | Autosave before doors, scripted teleports, interventions and paid travel. | TES3X | qol | development | by name |
| [`diagnostics`](../patches/diagnostics/) | Enable INI-controlled crash records, snapshots and a hang watchdog. | TES3X | instrumentation | development | development preset |
| [`console`](../patches/console/) | Make the in-game console reachable, by replacing its input gate. | TES3X | qol | development | standard preset |
| [`profile=VA[,VA...]`](../patches/profile/) | Time listed functions with RDTSC at every direct call site. | TES3X | instrumentation | development | build option |
| [`heap-census`](../patches/heap-census/) | Count live engine heap bytes by source file and line, or by call site. | TES3X | instrumentation | development | build option |
| [`mem-census`](../patches/mem-census/) | Count kernel allocations and the XAPI heap by caller, with the address space map. | TES3X | instrumentation | development | build option |
| [`heap-region`](../patches/heap-region/) | With more than 64 MB, size the engine heap's region from [Xbox] HeapRegionKB. | TES3X | compat | development | by name |
| `info-name-arena` | Page INFO's temporary ID, previous and next names outside the engine heap. | TES3X | compat | development | by name |
| `lean-menu` | Load only game settings for the main menu; New Game and Load relaunch and load the rest. | TES3X | performance | development | by name |
