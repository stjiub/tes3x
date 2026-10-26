# Patches

Generated from [`patches.toml`](../patches.toml) by `tes3x patches --write`;
edit that file, not this one. Fixes that are not implemented are in
[candidates.md](candidates.md). A linked title opens the patch's notes.

`dev` patches are contributor-only, `preview` patches work but need broader testing,
and `release` patches are ready for general use.
"By name" patches are only applied when a profile enables them.
"Included by" says what selects a patch; requirements are contextual rather than a
single required/not-required flag.


## Build and boot

| title | patch key | what it does | from | category | channel | included by |
|---|---|---|---|---|---|---|
| [Code payload injection](../patches/payload.md) | `payload=FILE.pe` | Inject a code section and run it from the entry point. | TES3X | infrastructure | preview | Injected-code patches or build option |
| [Unrestricted boot media](../patches/boot-media.md) | `boot-media` | Permit booting from any media and region, not just a retail DVD. | TES3X | infrastructure | preview | **Always included** |
| [Data Files drive redirect](../patches/drive-letters.md) | `drive-letters=LETTER` | Point every Data Files asset path at one drive. | TES3X | infrastructure | preview | **Always included** |
| [Same-volume save staging](../patches/save-staging.md) | `save-staging=LETTER` | Stage saves on one volume with UDATA so the commit renames instead of copying. | TES3X | infrastructure | preview | Build option |
| [Dashboard title](../patches/title.md) | `title=NAME` | Rename the XBE's certificate title, the name a dashboard falls back to. | TES3X | infrastructure | preview | Build option |
| [Separate save title ID](../patches/title-id.md) | `title-id=HEX` | Give the XBE its own title ID, so its saves go to a separate E:/UDATA folder. | TES3X | infrastructure | preview | Build option |
| [Multiple BSA loading](../patches/multi-bsa.md) | `multi-bsa` | Load every archive listed in tes3xarch.txt, not just Morrowind.bsa. | TES3X | infrastructure | preview | Delta-BSA packaging |
| [Game folder overlay](../patches/data-overlay.md) | `data-overlay` | Read files missing from the game folder from another install, named by [Xbox] OverlayBase. | TES3X | infrastructure | preview | Overlay install layout or profile |

## Script engine

| title | patch key | what it does | from | category | channel | included by |
|---|---|---|---|---|---|---|
| [Script opcode extensions](../patches/script-ext.md) | `script-ext` | Add script opcodes: widen the length bounds and hook Script::RunFunction. | TES3X | compat | preview | By name; `mwse-legacy` |
| [Legacy MWSE bytecode](../patches/mwse-legacy.md) | `mwse-legacy` | Interpret legacy MWSE 0.9.4 bytecode embedded in compiled scripts. | TES3X | compat | dev | By name |
| [Empty pathgrid fix](../patches/mwse-pathgrid-zero.md) | `mwse-pathgrid-zero` | Stop Wander and Flee picking a node from a pathgrid that has none. | [MWSE](https://mwse.github.io/MWSE/references/general/patches/) | core | dev | By name |
| [Enable and Disable without local variables](../patches/mwse-enable-disable-vars.md) | `mwse-enable-disable-vars` | Stop Enable and Disable crashing in a script that has no local variables. | [MWSE](https://mwse.github.io/MWSE/references/general/patches/) | core | dev | By name |

## MCP fixes

| title | patch key | what it does | from | category | channel | included by |
|---|---|---|---|---|---|---|
| [Unresolvable reference cleanup](../patches/mcp-1.md) | `mcp-1` | Stop an unresolvable reference being restamped as created at runtime. | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #1 | core | preview | testing preset |
| [Unarmored damage reduction](../patches/mcp-3.md) | `mcp-3` | Apply Unarmored damage reduction when no armor is equipped. | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #3 | correctness | preview | testing preset |
| [Restore fortified attributes](../patches/mcp-6.md) | `mcp-6` | Let Restore and expiring Drain recover the active temporary Fortify amount. | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #6 | correctness | dev | By name |
| [Blind attack penalty](../patches/mcp-25.md) | `mcp-25` | Make Blind lower the player's attack chance instead of raising it. | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #25 | correctness | dev | By name |
| [Projectile aiming fix](../patches/mcp-30.md) | `mcp-30` | Correct projectile aim for scaled races and in third person. | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #30 | correctness | dev | By name |
| [Cell-change casting crash fix](../patches/mcp-37.md) | `mcp-37` | Cancel stale NPC casts before a cell change leaves them dangling. | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #37 | core | preview | testing preset |
| [Spell deselection fix](../patches/mcp-47.md) | `mcp-47` | Keep another actor's magic-item changes from deselecting the player's spell. | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #47 | correctness | dev | By name |
| [Level-up multiplier fix](../patches/mcp-48.md) | `mcp-48` | Show earned attribute multipliers even when they raise an attribute to 100, and avoid overlapping x3 labels. | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #48 | correctness | dev | By name |
| [Alchemy potion naming and stacking](../patches/mcp-55.md) | `mcp-55` | Keep custom potion names after Create and regroup inventory icons when alchemy closes. | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #55 | correctness | dev | By name |
| [Summoned creature crash fix](../patches/mcp-92.md) | `mcp-92` | Retire a summoned actor's magic before the actor is destroyed. | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #92 | core | preview | testing preset |
| [Book and scroll scaling fix](../patches/mcp-94.md) | `mcp-94` | Scale books, the journal and scrolls by screen height, so they fit a widescreen picture. | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #94 | correctness | dev | By name; `hd-video` |
| [Saved-script initialization fix](../patches/mcp-97.md) | `mcp-97` | Advance the script parser correctly while initializing saved data. | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #97 | core | preview | testing preset |
| [Animated container crash fix](../patches/mcp-98.md) | `mcp-98` | Keep animated-container access from destroying a live animation. | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #98 | core | preview | testing preset |
| [Removed-mod object reactivation](../patches/mcp-102.md) | `mcp-102` | Reactivate script-triggered objects after their script mod is removed. | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #102 | correctness | preview | testing preset |
| [Airborne item drop fix](../patches/mcp-118.md) | `mcp-118` | Keep items dropped in third person while airborne near the player. | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #118 | core | dev | By name |
| [PlaceItem persistence](../patches/mcp-123.md) | `mcp-123` | Persist objects placed by scripts into cells the player has not visited. | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #123 | core | preview | testing preset |
| [Position actor initialization](../patches/mcp-125.md) | `mcp-125` | Initialize scripts and collision for actors moved by Position or PositionCell. | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #125 | core | preview | testing preset |
| [World map texture independence](../patches/mcp-127.md) | `mcp-127` | Keep replacement land textures from changing the world map panel size. | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #127 | compat | dev | By name |
| [Improved animation support](../patches/mcp-132.md) | `mcp-132` | Play scripted animations across NPC body layers, support extra bones and animate the player. | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #132 | compat | dev | By name; `cant-touch-this` |
| [Nocked arrow removal](../patches/mcp-146.md) | `mcp-146` | Remove a fully nocked arrow with the Ready Weapon control. | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #146 | qol | dev | By name |
| [Safe corpse disposal](../patches/mcp-149.md) | `mcp-149` | Keep corpse disposal unavailable until an actor finishes dying. | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #149 | core | dev | By name |
| [Script data overread fix](../patches/mcp-154.md) | `mcp-154` | Pad compiled script-data allocations to keep dword reads in bounds. | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #154 | core | dev | By name |
| [RemoveItem weight fix](../patches/mcp-160.md) | `mcp-160` | Reduce encumbrance for a removed item only when the item is actually in the inventory. | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #160 | correctness | dev | By name |
| [NPC faction rank persistence](../patches/mcp-173.md) | `mcp-173` | Save an NPC's faction rank after RaiseRank or LowerRank changes it. | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #173 | core | dev | By name |

## Memory

| title | patch key | what it does | from | category | channel | included by |
|---|---|---|---|---|---|---|
| [Expanded-memory video arena](../patches/video-arena.md) | `video-arena` | Size the texture and vertex buffer arena from [Xbox] VideoMemoryKB, and pack what overflows it into shared chunks. | TES3X | compat | dev | By name |
| [Expanded-memory heap region](../patches/heap-region.md) | `heap-region` | With more than 64 MB, size the engine heap's region from [Xbox] HeapRegionKB. | TES3X | compat | dev | By name |
| [Paged dialogue link names](../patches/info-name-arena.md) | `info-name-arena` | Page INFO's temporary ID, previous and next names outside the engine heap. | TES3X | compat | dev | By name |
| [Paged NPC and inventory link names](../patches/link-name-arena.md) | `link-name-arena` | Page NPCs' race, class, faction, head and hair names, inventory item IDs and the base records' inventories outside the engine heap. | TES3X | compat | dev | By name; `script-arena`, `path-arena`, `land-arena`, `npc-arena` |
| [Paged script bytecode](../patches/script-arena.md) | `script-arena` | Page compiled script bytecode outside the engine heap. | TES3X | compat | dev | By name |
| [Paged model paths, icon paths and display names](../patches/path-arena.md) | `path-arena` | Page the model and icon paths and display names of loaded records outside the engine heap. | TES3X | compat | dev | By name |
| [Paged land objects](../patches/land-arena.md) | `land-arena` | Page the land objects of loaded exterior cells outside the engine heap. | TES3X | compat | dev | By name |
| [Paged NPC objects](../patches/npc-arena.md) | `npc-arena` | Page the NPC records' objects outside the engine heap. | TES3X | compat | dev | By name |
| [Dialogue responses built on first use](../patches/lazy-dialogue.md) | `lazy-dialogue` | Load dialogue responses as file references and build a topic's responses when it is first needed. | TES3X | compat | dev | By name |
| [Growing small-block pools](../patches/pool-grow.md) | `pool-grow` | Add another pool when a small-block size class fills, instead of giving each further block a header in the heap. | TES3X | compat | dev | By name |

## Speed

| title | patch key | what it does | from | category | channel | included by |
|---|---|---|---|---|---|---|
| [Save and load without forced sleeps](../patches/loop-sleeps.md) | `loop-sleeps` | Yield instead of sleeping 1 ms every 16-128 objects in the save and load walks. | TES3X | performance | dev | By name |
| [Indexed reference lookup at global script start](../patches/ref-index.md) | `ref-index` | Find the references global scripts name from one pass over the cells, not one per name. | TES3X | performance | dev | By name; `npc-arena` |
| [Creature list for animation sound keys](../patches/soundgen-index.md) | `soundgen-index` | Match an animation's footstep and voice keys against the creatures, not every loaded object. | TES3X | performance | dev | By name; `npc-arena` |
| [Low-memory main menu](../patches/lean-menu.md) | `lean-menu` | Load only game settings for the main menu; New Game and Load relaunch and load the rest. | TES3X | performance | preview | By name |
| [Direct response topic lookup](../patches/dialogue-owner.md) | `dialogue-owner` | Find a response's topic directly instead of searching every topic's responses. | TES3X | performance | dev | By name |

## Interface and saves

| title | patch key | what it does | from | category | channel | included by |
|---|---|---|---|---|---|---|
| [Build-selected player preferences](../patches/build-preferences.md) | `build-preferences` | Apply profile-selected player preferences after stored Xbox options load. | TES3X | qol | preview | Build option |
| [Rotating autosaves](../patches/rotating-autosaves.md) | `rotating-autosaves` | Rotate automatic saves through INI-configurable slots. | [OpenMW](https://openmw.org) | qol | preview | By name |
| [Transition autosaves](../patches/transition-autosaves.md) | `transition-autosaves` | Autosave before doors, scripted teleports, interventions and paid travel. | TES3X | qol | preview | By name |
| [Lowered first-person bow](../patches/bow-view.md) | `bow-view` | Lower the first-person bow and arms while an arrow is nocked. | TES3X | qol | dev | By name |
| [HD video output](../patches/hd-video.md) | `hd-video` | Follow the dashboard's HDTV settings: 720p when enabled, otherwise the retail 480p or 480i. | TES3X | qol | dev | By name |
| [Extended tooltips](../patches/extended-tooltips.md) | `extended-tooltips` | Warn before stealing and show ownership and selected item details in menus. | TES3X | qol | dev | By name |
| [Read topic colors](../patches/topic-colors.md) | `topic-colors` | Show dialogue topics whose current response you have already read in a different color. | TES3X | qol | dev | By name |
| [UI scaling](../patches/ui-scale.md) | `ui-scale` | Scale text, fixed HUD art and common menu panels, with optional 480p widescreen correction. | TES3X | qol | dev | Build option |
| [Clear video memory at boot](../patches/boot-clear.md) | `boot-clear` | Clear the screen before the first frame, so a boot without the retail launcher does not flash green lines. | TES3X | qol | dev | By name; `dash-button`, `multiplayer` |
| [Main menu still](../patches/menu-still.md) | `menu-still` | Show a still image behind the main menu instead of playing the logo movie. | TES3X | qol | dev | By name; `dash-button`, `multiplayer` |
| [Menu hint line](../patches/dash-button.md) | `dash-button` | Add a hint line to the main and pause menus: White leaves for the Dash. | TES3X | qol | dev | By name; `multiplayer` |

## Network

| title | patch key | what it does | from | category | channel | included by |
|---|---|---|---|---|---|---|
| [Xbox network foundation](../patches/net.md) | `net` | Provide the shared Xbox NIC, ARP, IPv4 and UDP layer used by network services. | TES3X | infrastructure | dev | By name; `multiplayer`, `agent` |
| [Experimental multiplayer](../patches/multiplayer.md) | `multiplayer` | Join a TES3X server from [Xbox] NetAddress and exchange player states every frame. | TES3X | infrastructure | dev | By name |
| [In-game agent](../patches/agent.md) | `agent` | Report live status and logs to the paired TES3X GUI and take its commands. | TES3X | infrastructure | dev | By name |

## Instrumentation

| title | patch key | what it does | from | category | channel | included by |
|---|---|---|---|---|---|---|
| [In-game console](../patches/console.md) | `console` | Make the in-game console reachable, by replacing its input gate. | TES3X | instrumentation | preview | testing preset |
| [Crash and hang diagnostics](../patches/diagnostics.md) | `diagnostics` | Enable INI-controlled crash records, snapshots and a hang watchdog. | TES3X | instrumentation | preview | testing preset; `net` |
| [Function profiler](../patches/profile.md) | `profile=VA[,VA...]` | Time listed functions with RDTSC at every direct call site. | TES3X | instrumentation | preview | Build option |
| [Engine heap census](../patches/heap-census.md) | `heap-census` | Count live engine heap bytes by source file and line, or by call site. | TES3X | instrumentation | dev | Build option |
| [Kernel memory census](../patches/mem-census.md) | `mem-census` | Count kernel allocations and the XAPI heap by caller, with the address space map. | TES3X | instrumentation | dev | Build option |

## Other fixes

| title | patch key | what it does | from | category | channel | included by |
|---|---|---|---|---|---|---|
| [Keep responses of repeated topics](../patches/dialogue-merge.md) | `dialogue-merge` | Keep a topic's earlier responses when a later plugin adds to it. | TES3X | correctness | preview | testing preset; `lazy-dialogue` |
| [Can't Touch This dodges](../patches/cant-touch-this.md) | `cant-touch-this` | Play a dodge animation when a melee attack misses a calm NPC, using Can't Touch This's clips. | TES3X | compat | dev | By name |
| [DXT5 allocation fix](../patches/dxt5-size.md) | `dxt5-size` | Allocate DXT5 textures at their real size, not a negative one that corrupts video memory. | TES3X | core | preview | testing preset |
