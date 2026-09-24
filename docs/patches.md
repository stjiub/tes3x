# Patches

Generated from [`patches.toml`](../patches.toml) by `tools/tes3x_patches.py --write`.
Edit that file, not this one. Fixes that are not implemented, including every Morrowind
Code Patch fix, are listed in [candidates.md](candidates.md).

**Status**: `implemented` means the patch applies and passes structural checks;
`verified-xemu` means it was shown to work in the xemu emulator; `verified-hardware`
means it was shown to work on an original Xbox.

**Selected by**: `standard` and `development` are presets; a patch marked `standard` is
also in `development`. Anything else is selected explicitly for a build.

**Evidence**: the proof record behind a status: a control run without the patch, a run
with it, and their logs. A linked patch name opens its folder of notes and records.

| patch | what it does | from | category | status | selected by | evidence |
|---|---|---|---|---|---|---|
| `payload=FILE.pe` | Inject a code section and run it from the entry point. | TES3X | infrastructure | verified-xemu | build option | findings log, no record yet |
| `boot-media` | Permit booting from any media and region, not just a retail DVD. | TES3X | infrastructure | verified-xemu | every build | findings log, no record yet |
| `drive-letters=LETTER` | Point every Data Files asset path at one drive. | TES3X | infrastructure | verified-xemu | every build | findings log, no record yet |
| `save-staging=LETTER` | Stage saves on one volume with UDATA so the commit renames instead of copying. | TES3X | infrastructure | implemented | build option |  |
| `title=NAME` | Rename the XBE's certificate title, the name a dashboard falls back to. | TES3X | infrastructure | implemented | build option |  |
| `multi-bsa` | Load every archive listed in tes3xarch.txt, not just Morrowind.bsa. | TES3X | infrastructure | verified-xemu | delta-bsa packing | findings log, no record yet |
| `script-ext` | Add script opcodes: widen the length bounds and hook Script::RunFunction. | TES3X | compat | verified-xemu | by name | findings log, no record yet |
| [`mcp-1`](../patches/mcp-1/) | Stop an unresolvable reference being restamped as created at runtime. | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #1 | core | implemented | by name |  |
| [`mcp-97`](../patches/mcp-97/) | Advance the script parser correctly while initializing saved data. | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #97 | core | verified-xemu | standard | [2026-09-21-xemu](../patches/mcp-97/2026-09-21-xemu.toml) |
| [`mcp-154`](../patches/mcp-154/) | Pad compiled script-data allocations to keep dword reads in bounds. | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #154 | core | implemented | by name |  |
| [`mcp-102`](../patches/mcp-102/) | Reactivate script-triggered objects after their script mod is removed. | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #102 | correctness | verified-xemu | standard | [2026-09-21-xemu](../patches/mcp-102/2026-09-21-xemu.toml) |
| [`mcp-140`](../patches/mcp-140/) | Throttle loading-screen redraws to one every 50 milliseconds. | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #140 | performance | implemented | by name |  |
| [`dxt5-size`](../patches/dxt5-size/) | Allocate DXT5 textures at their real size, not a negative one that corrupts video memory. | TES3X | core | verified-xemu | standard | [2026-09-23-xemu](../patches/dxt5-size/2026-09-23-xemu.toml) |
| [`rotating-autosaves`](../patches/rotating-autosaves/) | Rotate automatic saves through INI-configurable slots. | [OpenMW](https://openmw.org) | qol | verified-xemu | by name | [2026-09-23-xemu](../patches/rotating-autosaves/2026-09-23-xemu.toml) |
| [`build-preferences`](../patches/build-preferences/) | Apply profile-selected player preferences after stored Xbox options load. | TES3X | qol | implemented | build option |  |
| [`transition-autosaves`](../patches/transition-autosaves/) | Autosave before doors, scripted teleports, interventions and paid travel. | TES3X | qol | verified-xemu | by name | [2026-09-23-xemu](../patches/transition-autosaves/2026-09-23-xemu.toml) |
| [`diagnostics`](../patches/diagnostics/) | Enable INI-controlled crash records, snapshots and a hang watchdog. | TES3X | instrumentation | verified-xemu | development | findings log, no record yet |
| [`console`](../patches/console/) | Make the in-game console reachable, by replacing its input gate. | TES3X | qol | verified-xemu | development | [2026-09-23-xemu](../patches/console/2026-09-23-xemu.toml) |
| [`profile=VA[,VA...]`](../patches/profile/) | Time listed functions with RDTSC at every direct call site. | TES3X | instrumentation | implemented | build option |  |
