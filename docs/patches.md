# Patches

Generated from [`patches.toml`](../patches.toml) by `tools/tes3x_patches.py --write`.
Edit that file, not this one. Fixes that are not implemented, including every Morrowind
Code Patch fix, are listed in [candidates.md](candidates.md).

**Channel**: every implemented patch starts in `development`. A patch moves to
`release` only when the maintainer decides it is ready after appropriate validation and
testing. Automated results never promote a patch.

**Selected by**: the `standard` preset includes release-channel core and correctness
patches. `development` also includes their development-channel counterparts and test
instrumentation. Anything else is selected explicitly for a build.

**Validation**: repeatable scenarios and recorded results. A result says only what its
scenario observed on that platform; it is not a release decision. A linked patch name
opens its folder of notes, scenarios and results.

| patch | what it does | from | category | channel | selected by | validation |
|---|---|---|---|---|---|---|
| `payload=FILE.pe` | Inject a code section and run it from the entry point. | TES3X | infrastructure | development | build option | findings log, no record yet |
| `boot-media` | Permit booting from any media and region, not just a retail DVD. | TES3X | infrastructure | development | every build | findings log, no record yet |
| `drive-letters=LETTER` | Point every Data Files asset path at one drive. | TES3X | infrastructure | development | every build | findings log, no record yet |
| `save-staging=LETTER` | Stage saves on one volume with UDATA so the commit renames instead of copying. | TES3X | infrastructure | development | build option |  |
| `title=NAME` | Rename the XBE's certificate title, the name a dashboard falls back to. | TES3X | infrastructure | development | build option |  |
| `multi-bsa` | Load every archive listed in tes3xarch.txt, not just Morrowind.bsa. | TES3X | infrastructure | development | delta-bsa packing | findings log, no record yet |
| `script-ext` | Add script opcodes: widen the length bounds and hook Script::RunFunction. | TES3X | compat | development | by name | findings log, no record yet |
| `mwse-legacy` | Interpret legacy MWSE 0.9.4 bytecode embedded in compiled scripts. | TES3X | compat | development | by name |  |
| [`mcp-1`](../patches/mcp-1/) | Stop an unresolvable reference being restamped as created at runtime. | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #1 | core | development | development preset |  |
| [`mcp-97`](../patches/mcp-97/) | Advance the script parser correctly while initializing saved data. | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #97 | core | development | development preset | [2026-09-21-xemu](../patches/mcp-97/2026-09-21-xemu.toml) |
| [`mcp-154`](../patches/mcp-154/) | Pad compiled script-data allocations to keep dword reads in bounds. | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #154 | core | development | development preset |  |
| [`mcp-102`](../patches/mcp-102/) | Reactivate script-triggered objects after their script mod is removed. | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #102 | correctness | development | development preset | [2026-09-21-xemu](../patches/mcp-102/2026-09-21-xemu.toml) |
| [`mcp-140`](../patches/mcp-140/) | Throttle loading-screen redraws to one every 50 milliseconds. | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #140 | performance | development | by name |  |
| [`dxt5-size`](../patches/dxt5-size/) | Allocate DXT5 textures at their real size, not a negative one that corrupts video memory. | TES3X | core | development | development preset | [2026-09-23-xemu](../patches/dxt5-size/2026-09-23-xemu.toml) |
| [`video-arena`](../patches/video-arena/) | With more than 64 MB, size the texture and vertex buffer arena from [Xbox] VideoMemoryKB. | TES3X | compat | development | by name |  |
| [`rotating-autosaves`](../patches/rotating-autosaves/) | Rotate automatic saves through INI-configurable slots. | [OpenMW](https://openmw.org) | qol | development | by name | [2026-09-23-xemu](../patches/rotating-autosaves/2026-09-23-xemu.toml) |
| [`build-preferences`](../patches/build-preferences/) | Apply profile-selected player preferences after stored Xbox options load. | TES3X | qol | development | build option | [2026-09-24-xemu](../patches/build-preferences/2026-09-24-xemu.toml) |
| [`transition-autosaves`](../patches/transition-autosaves/) | Autosave before doors, scripted teleports, interventions and paid travel. | TES3X | qol | development | by name | [2026-09-23-xemu](../patches/transition-autosaves/2026-09-23-xemu.toml) |
| [`diagnostics`](../patches/diagnostics/) | Enable INI-controlled crash records, snapshots and a hang watchdog. | TES3X | instrumentation | development | development preset | findings log, no record yet |
| [`console`](../patches/console/) | Make the in-game console reachable, by replacing its input gate. | TES3X | qol | development | development preset | [2026-09-23-xemu](../patches/console/2026-09-23-xemu.toml) |
| [`profile=VA[,VA...]`](../patches/profile/) | Time listed functions with RDTSC at every direct call site. | TES3X | instrumentation | development | build option | [2026-09-23-xemu](../patches/profile/2026-09-23-xemu.toml) |
| [`heap-census`](../patches/heap-census/) | Count live engine heap bytes by source file and line, or by call site. | TES3X | instrumentation | development | build option |  |
| [`mem-census`](../patches/mem-census/) | Count kernel allocations and the XAPI heap by caller, with the address space map. | TES3X | instrumentation | development | build option |  |
| [`heap-region`](../patches/heap-region/) | With more than 64 MB, size the engine heap's region from [Xbox] HeapRegionKB. | TES3X | compat | development | by name | [2026-09-24-xemu](../patches/heap-region/2026-09-24-xemu.toml) |
