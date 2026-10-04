# Patches

Generated from [`patches.toml`](../patches.toml) by `tools/tes3x_patches.py --write`;
edit that file, not this one. Fixes that are not implemented are in
[candidates.md](candidates.md). A linked title opens the patch's notes; a game-test link
opens the runnable test definition.

`dev` patches are contributor-only, `preview` patches work but need broader testing,
and `release` patches are ready for general use.
"By name" patches are only applied when a profile enables them.
"Included by" says what selects a patch; requirements are contextual rather than a
single required/not-required flag.
The game-test column shows whether a public test definition exists, not its result.

| title | patch key | what it does | from | category | channel | game test | included by |
|---|---|---|---|---|---|---|---|
| [Code payload injection](../patches/payload.md) | `payload=FILE.pe` | Inject a code section and run it from the entry point. | TES3X | infrastructure | dev | — | Injected-code patches or build option |
| [Unrestricted boot media](../patches/boot-media.md) | `boot-media` | Permit booting from any media and region, not just a retail DVD. | TES3X | infrastructure | preview | [test](../tests/game/boot-media.toml) | **Always included** |
| [Data Files drive redirect](../patches/drive-letters.md) | `drive-letters=LETTER` | Point every Data Files asset path at one drive. | TES3X | infrastructure | preview | [test](../tests/game/drive-letters.toml) | **Always included** |
| [Same-volume save staging](../patches/save-staging.md) | `save-staging=LETTER` | Stage saves on one volume with UDATA so the commit renames instead of copying. | TES3X | infrastructure | preview | — | Build option |
| [Save and load without forced sleeps](../patches/loop-sleeps.md) | `loop-sleeps` | Yield instead of sleeping 1 ms every 16-128 objects in the save and load walks. | TES3X | performance | dev | [test](../tests/game/loop-sleeps.toml) | By name |
| [Indexed reference lookup at global script start](../patches/ref-index.md) | `ref-index` | Find the references global scripts name from one pass over the cells, not one per name. | TES3X | performance | dev | [test](../tests/game/ref-index.toml) | By name |
| [Dashboard title](../patches/title.md) | `title=NAME` | Rename the XBE's certificate title, the name a dashboard falls back to. | TES3X | infrastructure | preview | [test](../tests/game/title.toml) | Build option |
| [Separate save title ID](../patches/title-id.md) | `title-id=HEX` | Give the XBE its own title ID, so its saves go to a separate E:/UDATA folder. | TES3X | infrastructure | preview | [test](../tests/game/title-id.toml) | Build option |
| [Multiple BSA loading](../patches/multi-bsa.md) | `multi-bsa` | Load every archive listed in tes3xarch.txt, not just Morrowind.bsa. | TES3X | infrastructure | preview | [test](../tests/game/multi-bsa.toml) | Delta-BSA packaging |
| [Script opcode extensions](../patches/script-ext.md) | `script-ext` | Add script opcodes: widen the length bounds and hook Script::RunFunction. | TES3X | compat | preview | [test](../tests/game/script-ext.toml) | By name; `mwse-legacy` |
| [Legacy MWSE bytecode](../patches/mwse-legacy.md) | `mwse-legacy` | Interpret legacy MWSE 0.9.4 bytecode embedded in compiled scripts. | TES3X | compat | dev | — | By name |
| [Unresolvable reference cleanup](../patches/mcp-1.md) | `mcp-1` | Stop an unresolvable reference being restamped as created at runtime. | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #1 | core | dev | — | By name |
| [Unarmored damage reduction](../patches/mcp-3.md) | `mcp-3` | Apply Unarmored damage reduction when no armor is equipped. | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #3 | correctness | preview | [test](../tests/game/mcp-3.toml) | testing preset |
| [Keep responses of repeated topics](../patches/dialogue-merge.md) | `dialogue-merge` | Keep a topic's earlier responses when a later plugin adds to it. | TES3X | correctness | preview | [test](../tests/game/dialogue-merge.toml) | testing preset |
| [Cell-change casting crash fix](../patches/mcp-37.md) | `mcp-37` | Cancel stale NPC casts before a cell change leaves them dangling. | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #37 | core | dev | [test](../tests/game/mcp-37.toml) | By name |
| [Summoned creature crash fix](../patches/mcp-92.md) | `mcp-92` | Retire a summoned actor's magic before the actor is destroyed. | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #92 | core | dev | [test](../tests/game/mcp-92.toml) | By name |
| [Saved-script initialization fix](../patches/mcp-97.md) | `mcp-97` | Advance the script parser correctly while initializing saved data. | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #97 | core | preview | [test](../tests/game/mcp-97.toml) | testing preset |
| [Animated container crash fix](../patches/mcp-98.md) | `mcp-98` | Keep animated-container access from destroying a live animation. | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #98 | core | dev | [test](../tests/game/mcp-98.toml) | By name |
| [Removed-mod object reactivation](../patches/mcp-102.md) | `mcp-102` | Reactivate script-triggered objects after their script mod is removed. | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #102 | correctness | preview | [test](../tests/game/mcp-102.toml) | testing preset |
| [PlaceItem persistence](../patches/mcp-123.md) | `mcp-123` | Persist objects placed by scripts into cells the player has not visited. | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #123 | core | dev | [test](../tests/game/mcp-123.toml) | By name |
| [Position actor initialization](../patches/mcp-125.md) | `mcp-125` | Initialize scripts and collision for actors moved by Position or PositionCell. | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #125 | core | dev | [test](../tests/game/mcp-125.toml) | By name |
| [Nocked arrow removal](../patches/mcp-146.md) | `mcp-146` | Remove a fully nocked arrow with the Ready Weapon control. | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #146 | qol | dev | [test](../tests/game/mcp-146.toml) | By name |
| [Script data overread fix](../patches/mcp-154.md) | `mcp-154` | Pad compiled script-data allocations to keep dword reads in bounds. | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #154 | core | dev | — | By name |
| [DXT5 allocation fix](../patches/dxt5-size.md) | `dxt5-size` | Allocate DXT5 textures at their real size, not a negative one that corrupts video memory. | TES3X | core | preview | [test](../tests/game/dxt5-size.toml) | testing preset |
| [In-game console](../patches/console.md) | `console` | Make the in-game console reachable, by replacing its input gate. | TES3X | instrumentation | preview | [test](../tests/game/console.toml) | testing preset |
| [Crash and hang diagnostics](../patches/diagnostics.md) | `diagnostics` | Enable INI-controlled crash records, snapshots and a hang watchdog. | TES3X | instrumentation | preview | [test](../tests/game/diagnostics.toml) | testing preset; `net` |
| [Game folder overlay](../patches/data-overlay.md) | `data-overlay` | Read files missing from the game folder from another install, named by [Xbox] OverlayBase. | TES3X | infrastructure | dev | — | Overlay install layout or profile |
| [Function profiler](../patches/profile.md) | `profile=VA[,VA...]` | Time listed functions with RDTSC at every direct call site. | TES3X | instrumentation | preview | [test](../tests/game/profile.toml) | Build option |
| [Build-selected player preferences](../patches/build-preferences.md) | `build-preferences` | Apply profile-selected player preferences after stored Xbox options load. | TES3X | qol | preview | [test](../tests/game/build-preferences.toml) | Build option |
| [Rotating autosaves](../patches/rotating-autosaves.md) | `rotating-autosaves` | Rotate automatic saves through INI-configurable slots. | [OpenMW](https://openmw.org) | qol | dev | [test](../tests/game/rotating-autosaves.toml) | By name |
| [Transition autosaves](../patches/transition-autosaves.md) | `transition-autosaves` | Autosave before doors, scripted teleports, interventions and paid travel. | TES3X | qol | dev | [test](../tests/game/transition-autosaves.toml) | By name |
| [Low-memory main menu](../patches/lean-menu.md) | `lean-menu` | Load only game settings for the main menu; New Game and Load relaunch and load the rest. | TES3X | performance | dev | — | By name |
| [Lowered first-person bow](../patches/bow-view.md) | `bow-view` | Lower the first-person bow and arms while an arrow is nocked. | TES3X | qol | dev | [test](../tests/game/bow-view.toml) | By name |
| [Xbox network foundation](../patches/net.md) | `net` | Provide the shared Xbox NIC, ARP, IPv4 and UDP layer used by network services. | TES3X | infrastructure | dev | — | By name; `multiplayer`, `agent` |
| [Experimental multiplayer](../patches/multiplayer.md) | `multiplayer` | Join a TES3X server from [Xbox] NetAddress and exchange player states every frame. | TES3X | infrastructure | dev | — | By name |
| [In-game agent](../patches/agent.md) | `agent` | Report live game status and logs to the authenticated TES3X GUI listener. | TES3X | infrastructure | dev | — | By name |
| [Expanded-memory video arena](../patches/video-arena.md) | `video-arena` | With more than 64 MB, size the texture and vertex buffer arena from [Xbox] VideoMemoryKB. | TES3X | compat | dev | — | By name |
| [Engine heap census](../patches/heap-census.md) | `heap-census` | Count live engine heap bytes by source file and line, or by call site. | TES3X | instrumentation | dev | — | Build option |
| [Kernel memory census](../patches/mem-census.md) | `mem-census` | Count kernel allocations and the XAPI heap by caller, with the address space map. | TES3X | instrumentation | dev | — | Build option |
| [Expanded-memory heap region](../patches/heap-region.md) | `heap-region` | With more than 64 MB, size the engine heap's region from [Xbox] HeapRegionKB. | TES3X | compat | dev | [test](../tests/game/heap-region.toml) | By name |
| [Paged dialogue link names](../patches/info-name-arena.md) | `info-name-arena` | Page INFO's temporary ID, previous and next names outside the engine heap. | TES3X | compat | dev | — | By name |
