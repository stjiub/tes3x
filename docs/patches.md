# Patches

Generated from [`patches.toml`](../patches.toml) by `tools/tes3x_patches.py --write`;
edit that file, not this one. Fixes that are not implemented are in
[candidates.md](candidates.md). A linked patch name opens its notes; a game-test link
opens the runnable test definition.

`dev` patches are contributor-only, `preview` patches work but need broader testing,
and `release` patches are ready for general use.
"By name" patches are only applied when a profile enables them.
The game-test column shows whether a public test definition exists, not its result.

| patch | what it does | from | category | channel | game test | selected by |
|---|---|---|---|---|---|---|
| [`payload=FILE.pe`](../patches/payload.md) | Inject a code section and run it from the entry point. | TES3X | infrastructure | dev | — | build option |
| [`boot-media`](../patches/boot-media.md) | Permit booting from any media and region, not just a retail DVD. | TES3X | infrastructure | preview | [test](../tests/game/boot-media.toml) | every build |
| [`drive-letters=LETTER`](../patches/drive-letters.md) | Point every Data Files asset path at one drive. | TES3X | infrastructure | preview | [test](../tests/game/drive-letters.toml) | every build |
| [`save-staging=LETTER`](../patches/save-staging.md) | Stage saves on one volume with UDATA so the commit renames instead of copying. | TES3X | infrastructure | preview | — | build option |
| [`loop-sleeps`](../patches/loop-sleeps.md) | Yield instead of sleeping 1 ms every 16-128 objects in the save and load walks. | TES3X | performance | dev | [test](../tests/game/loop-sleeps.toml) | by name |
| [`title=NAME`](../patches/title.md) | Rename the XBE's certificate title, the name a dashboard falls back to. | TES3X | infrastructure | preview | [test](../tests/game/title.toml) | build option |
| [`title-id=HEX`](../patches/title-id.md) | Give the XBE its own title ID, so its saves go to a separate E:/UDATA folder. | TES3X | infrastructure | preview | [test](../tests/game/title-id.toml) | build option |
| [`multi-bsa`](../patches/multi-bsa.md) | Load every archive listed in tes3xarch.txt, not just Morrowind.bsa. | TES3X | infrastructure | preview | [test](../tests/game/multi-bsa.toml) | delta-bsa packing |
| [`script-ext`](../patches/script-ext.md) | Add script opcodes: widen the length bounds and hook Script::RunFunction. | TES3X | compat | preview | [test](../tests/game/script-ext.toml) | by name |
| [`mwse-legacy`](../patches/mwse-legacy.md) | Interpret legacy MWSE 0.9.4 bytecode embedded in compiled scripts. | TES3X | compat | dev | — | by name |
| [`mcp-1`](../patches/mcp-1.md) | Stop an unresolvable reference being restamped as created at runtime. | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #1 | core | dev | — | by name |
| [`mcp-3`](../patches/mcp-3.md) | Apply Unarmored damage reduction when no armor is equipped. | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #3 | correctness | preview | [test](../tests/game/mcp-3.toml) | testing preset |
| [`mcp-37`](../patches/mcp-37.md) | Cancel stale NPC casts before a cell change leaves them dangling. | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #37 | core | dev | [test](../tests/game/mcp-37.toml) | by name |
| [`mcp-92`](../patches/mcp-92.md) | Retire a summoned actor's magic before the actor is destroyed. | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #92 | core | dev | [test](../tests/game/mcp-92.toml) | by name |
| [`mcp-97`](../patches/mcp-97.md) | Advance the script parser correctly while initializing saved data. | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #97 | core | preview | [test](../tests/game/mcp-97.toml) | testing preset |
| [`mcp-98`](../patches/mcp-98.md) | Keep animated-container access from destroying a live animation. | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #98 | core | dev | [test](../tests/game/mcp-98.toml) | by name |
| [`mcp-102`](../patches/mcp-102.md) | Reactivate script-triggered objects after their script mod is removed. | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #102 | correctness | preview | [test](../tests/game/mcp-102.toml) | testing preset |
| [`mcp-123`](../patches/mcp-123.md) | Persist objects placed by scripts into cells the player has not visited. | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #123 | core | dev | [test](../tests/game/mcp-123.toml) | by name |
| [`mcp-125`](../patches/mcp-125.md) | Initialize scripts and collision for actors moved by Position or PositionCell. | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #125 | core | dev | [test](../tests/game/mcp-125.toml) | by name |
| [`mcp-146`](../patches/mcp-146.md) | Remove a fully nocked arrow with the Ready Weapon control. | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #146 | qol | dev | [test](../tests/game/mcp-146.toml) | by name |
| [`mcp-154`](../patches/mcp-154.md) | Pad compiled script-data allocations to keep dword reads in bounds. | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #154 | core | dev | — | by name |
| [`dxt5-size`](../patches/dxt5-size.md) | Allocate DXT5 textures at their real size, not a negative one that corrupts video memory. | TES3X | core | preview | [test](../tests/game/dxt5-size.toml) | testing preset |
| [`console`](../patches/console.md) | Make the in-game console reachable, by replacing its input gate. | TES3X | qol | preview | [test](../tests/game/console.toml) | testing preset |
| [`diagnostics`](../patches/diagnostics.md) | Enable INI-controlled crash records, snapshots and a hang watchdog. | TES3X | instrumentation | preview | [test](../tests/game/diagnostics.toml) | testing preset |
| [`data-overlay`](../patches/data-overlay.md) | Read files missing from the game folder from another install, named by [Xbox] OverlayBase. | TES3X | infrastructure | dev | — | by name |
| [`profile=VA[,VA...]`](../patches/profile.md) | Time listed functions with RDTSC at every direct call site. | TES3X | instrumentation | preview | [test](../tests/game/profile.toml) | build option |
| [`build-preferences`](../patches/build-preferences.md) | Apply profile-selected player preferences after stored Xbox options load. | TES3X | qol | preview | [test](../tests/game/build-preferences.toml) | build option |
| [`rotating-autosaves`](../patches/rotating-autosaves.md) | Rotate automatic saves through INI-configurable slots. | [OpenMW](https://openmw.org) | qol | dev | [test](../tests/game/rotating-autosaves.toml) | by name |
| [`transition-autosaves`](../patches/transition-autosaves.md) | Autosave before doors, scripted teleports, interventions and paid travel. | TES3X | qol | dev | [test](../tests/game/transition-autosaves.toml) | by name |
| [`lean-menu`](../patches/lean-menu.md) | Load only game settings for the main menu; New Game and Load relaunch and load the rest. | TES3X | performance | dev | — | by name |
| [`bow-view`](../patches/bow-view.md) | Lower the first-person bow and arms while an arrow is nocked. | TES3X | qol | dev | [test](../tests/game/bow-view.toml) | by name |
| [`multiplayer`](../patches/multiplayer.md) | Join a TES3X server from [Xbox] NetAddress and exchange player states every frame. | TES3X | infrastructure | dev | — | by name |
| [`video-arena`](../patches/video-arena.md) | With more than 64 MB, size the texture and vertex buffer arena from [Xbox] VideoMemoryKB. | TES3X | compat | dev | — | by name |
| [`heap-census`](../patches/heap-census.md) | Count live engine heap bytes by source file and line, or by call site. | TES3X | instrumentation | dev | — | build option |
| [`mem-census`](../patches/mem-census.md) | Count kernel allocations and the XAPI heap by caller, with the address space map. | TES3X | instrumentation | dev | — | build option |
| [`heap-region`](../patches/heap-region.md) | With more than 64 MB, size the engine heap's region from [Xbox] HeapRegionKB. | TES3X | compat | dev | [test](../tests/game/heap-region.toml) | by name |
| [`info-name-arena`](../patches/info-name-arena.md) | Page INFO's temporary ID, previous and next names outside the engine heap. | TES3X | compat | dev | — | by name |
