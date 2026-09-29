# Patches

Generated from [`patches.toml`](../patches.toml) by `tools/tes3x_patches.py --write`;
edit that file, not this one. Fixes that are not implemented are in
[candidates.md](candidates.md). A linked name opens the patch's folder of notes and tests.

`dev` patches are contributor-only, `preview` patches work but need broader testing,
and `release` patches are ready for general use.
"By name" patches are only applied when a profile enables them.

| patch | what it does | from | category | channel | selected by |
|---|---|---|---|---|---|
| `payload=FILE.pe` | Inject a code section and run it from the entry point. | TES3X | infrastructure | dev | build option |
| `boot-media` | Permit booting from any media and region, not just a retail DVD. | TES3X | infrastructure | preview | every build |
| `drive-letters=LETTER` | Point every Data Files asset path at one drive. | TES3X | infrastructure | preview | every build |
| `save-staging=LETTER` | Stage saves on one volume with UDATA so the commit renames instead of copying. | TES3X | infrastructure | dev | build option |
| `title=NAME` | Rename the XBE's certificate title, the name a dashboard falls back to. | TES3X | infrastructure | preview | build option |
| `title-id=HEX` | Give the XBE its own title ID, so its saves go to a separate E:/UDATA folder. | TES3X | infrastructure | preview | build option |
| `multi-bsa` | Load every archive listed in tes3xarch.txt, not just Morrowind.bsa. | TES3X | infrastructure | preview | delta-bsa packing |
| `script-ext` | Add script opcodes: widen the length bounds and hook Script::RunFunction. | TES3X | compat | preview | by name |
| `mwse-legacy` | Interpret legacy MWSE 0.9.4 bytecode embedded in compiled scripts. | TES3X | compat | dev | by name |
| [`mcp-1`](../patches/mcp-1.md) | Stop an unresolvable reference being restamped as created at runtime. | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #1 | core | dev | by name |
| [`mcp-37`](../patches/mcp-37.md) | Cancel stale NPC casts before a cell change leaves them dangling. | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #37 | core | dev | by name |
| [`mcp-97`](../patches/mcp-97.md) | Advance the script parser correctly while initializing saved data. | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #97 | core | preview | testing preset |
| [`mcp-98`](../patches/mcp-98.md) | Keep animated-container access from destroying a live animation. | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #98 | core | dev | by name |
| [`mcp-154`](../patches/mcp-154.md) | Pad compiled script-data allocations to keep dword reads in bounds. | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #154 | core | dev | by name |
| [`mcp-102`](../patches/mcp-102.md) | Reactivate script-triggered objects after their script mod is removed. | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #102 | correctness | preview | testing preset |
| [`dxt5-size`](../patches/dxt5-size.md) | Allocate DXT5 textures at their real size, not a negative one that corrupts video memory. | TES3X | core | preview | testing preset |
| [`video-arena`](../patches/video-arena.md) | With more than 64 MB, size the texture and vertex buffer arena from [Xbox] VideoMemoryKB. | TES3X | compat | dev | by name |
| [`rotating-autosaves`](../patches/rotating-autosaves.md) | Rotate automatic saves through INI-configurable slots. | [OpenMW](https://openmw.org) | qol | preview | by name |
| [`build-preferences`](../patches/build-preferences.md) | Apply profile-selected player preferences after stored Xbox options load. | TES3X | qol | preview | build option |
| [`transition-autosaves`](../patches/transition-autosaves.md) | Autosave before doors, scripted teleports, interventions and paid travel. | TES3X | qol | preview | by name |
| [`diagnostics`](../patches/diagnostics.md) | Enable INI-controlled crash records, snapshots and a hang watchdog. | TES3X | instrumentation | preview | testing preset |
| [`console`](../patches/console.md) | Make the in-game console reachable, by replacing its input gate. | TES3X | qol | preview | testing preset |
| [`profile=VA[,VA...]`](../patches/profile.md) | Time listed functions with RDTSC at every direct call site. | TES3X | instrumentation | preview | build option |
| [`heap-census`](../patches/heap-census.md) | Count live engine heap bytes by source file and line, or by call site. | TES3X | instrumentation | dev | build option |
| [`mem-census`](../patches/mem-census.md) | Count kernel allocations and the XAPI heap by caller, with the address space map. | TES3X | instrumentation | dev | build option |
| [`heap-region`](../patches/heap-region.md) | With more than 64 MB, size the engine heap's region from [Xbox] HeapRegionKB. | TES3X | compat | preview | by name |
| `info-name-arena` | Page INFO's temporary ID, previous and next names outside the engine heap. | TES3X | compat | dev | by name |
| `lean-menu` | Load only game settings for the main menu; New Game and Load relaunch and load the rest. | TES3X | performance | dev | by name |
| [`mcp-146`](../patches/mcp-146.md) | Remove a fully nocked arrow with the Ready Weapon control. | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #146 | qol | dev | by name |
| [`bow-view`](../patches/bow-view.md) | Lower the first-person bow and arms while an arrow is nocked. | TES3X | qol | dev | by name |
| [`multiplayer`](../patches/multiplayer.md) | Join a TES3X server from [Xbox] NetAddress and exchange player states every frame. | TES3X | infrastructure | dev | by name |
