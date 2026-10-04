# Commands

Every TES3X command is a Python script in `tools/`, run from the repository root. This page says
what each one is for and where it is explained; `--help` on any command lists its options.

## Building and deploying

| command | job | see |
|---|---|---|
| `tes3x_gui.py` | the GUI: profiles, mods, plugins, patches, INI, build, play and deploy | [GUI](gui.md) |
| `tes3x_pipeline.py` | build one profile into a game folder, and optionally deploy it | [pipeline](pipeline.md) |
| `tes3x_build.py` | collect a profile's mods into one `Data Files` tree; report conflicts, textures and missing masters; prune unused assets | [pipeline](pipeline.md#2-collect-the-winning-files), [packaging](packaging.md#pruning-unused-assets) |
| `tes3x_plugins.py` | order plugins with mlox or a saved order; download the mlox rules | [pipeline](pipeline.md#sorting-plugins-with-mlox) |
| `tes3x_optimize.py` | write equivalence-checked derived copies that make the Xbox load fewer records | [plugin optimizer](optimizer.md) |
| `tes3x_pack.py` | pack a collected tree into archives and stage the game's `Data Files` | [packaging](packaging.md) |
| `tes3x_payload.py` | compile the patch payload for one retail XBE | [payload](../patches/payload.md) |
| `tes3x_patch.py` | apply patches to an XBE directly; `--list` shows them | [patches](patches.md) |
| `tes3x_deploy.py` | upload a game folder to the Xbox | [deployment](deployment.md) |
| `tes3x_fetch.py` | copy files or folders off the Xbox | [deployment](deployment.md#getting-files-back) |
| `tes3x_saves.py` | list and move saves between the Xbox, an xemu disk and a PC save library | [GUI](gui.md) |

## Mods

| command | job | see |
|---|---|---|
| `tes3x_library.py` | check, scan or convert a versioned mod library | [mod library](mod-library.md) |
| `tes3x_nexus.py` | look a mod up on Nexus Mods | [GUI](gui.md) |
| `tes3x_audit.py` | check a `Data Files` folder for long names, junk files and duplicates | [packaging](packaging.md#sound-and-mesh-checks) |
| `tes3x_assets.py` | check NIF texture references and WAV formats; resample WAVs | [packaging](packaging.md#sound-and-mesh-checks) |
| `tes3x_mwse.py` | find legacy MWSE bytecode in compiled plugins | [legacy MWSE bytecode](../patches/mwse-legacy.md) |
| `tes3x_map.py` | inspect a map companion file such as `Morrowind.esm.map` | [packaging](packaging.md#sound-and-mesh-checks) |

## Testing

| command | job | see |
|---|---|---|
| `tes3x_xemu.py` | build and run a profile in xemu, interactively or from an exec script | [xemu](xemu.md) |
| `tes3x_xemu_setup.py` | find xemu's files in one folder, or download xemu and a blank disk | [xemu](xemu.md) |
| `tes3x_test.py` | smoke-test a profile in xemu, or every library mod on its own | [testing](testing.md#profile-smoke-tests) |
| `tes3x_scenario.py` | run a patch's game test and apply its verdict | [validation](validation.md) |
| `tes3x_validate.py` | record and check local game-test results | [validation](validation.md) |
| `tes3x_tour.py` | write a script that tours a plugin's cells logging memory; summarise the log | [diagnostics](diagnostics.md#memory-tours) |
| `tes3x_orphan.py` | write a test plugin with references that cannot resolve | [unresolvable reference cleanup](../patches/mcp-1.md) |
| `tes3x_scriptasm.py` | write a plugin that calls a new script opcode | [script opcode extensions](../patches/script-ext.md) |

## Diagnostics

| command | job | see |
|---|---|---|
| `tes3x_diag.py` | fetch and summarise the diagnostics log | [diagnostics](diagnostics.md#the-log) |
| `tes3x_prof.py` | pick profiler targets by name; render the profiler dump | [diagnostics](diagnostics.md#profiling) |
| `tes3x_heap.py` | render the engine heap census | [diagnostics](diagnostics.md#heap-census) |
| `tes3x_mem.py` | render the kernel memory census | [diagnostics](diagnostics.md#memory-census) |
| `tes3x_readlog.py` | read the log or another file from an xemu disk image | [xemu](xemu.md) |
| `tes3x_ess.py` | break a save into record types, changed references and inventories | |

## Multiplayer

| command | job | see |
|---|---|---|
| `tes3x_net.py` | run a multiplayer server, administer it, and test the network driver | [multiplayer](multiplayer.md) |
| `tes3x_agent.py` | listen for authenticated in-game status and log packets | [in-game agent](../patches/agent.md) |
| `tes3x_menuart.py` | redraw the menu buttons multiplayer ships, in `assets/menu` (needs Pillow and numpy) | [multiplayer](multiplayer.md) |

## Reverse engineering

| command | job | see |
|---|---|---|
| `tes3x_sym.py` | build and query the XBE's symbol map; decompile through Ghidra | [symbol map](symbol-map.md) |
| `tes3x_layouts.py` | compile MWSE's struct layouts into data types for Ghidra | [symbol map](symbol-map.md#types) |
| `tes3x_xbe.py` | print an XBE's header, certificate, sections and strings | |
| `tes3x_disasm.py` | disassemble an address range, naming calls from the symbol map, and find references and direct calls to an address | |
| `tes3x_inject.py` | add a section to an XBE and retarget its entry point | [payload](../patches/payload.md) |
| `tes3x_ini.py` | list every `Morrowind.ini` key the XBE reads, with its default | [ini keys](ini-keys.md) |
| `tes3x_mcp.py` | read a Morrowind Code Patch fix against the original PC executable | [patch policy](patch-policy.md) |
| `tes3x_patches.py` | check the patch table and regenerate its pages | [patch notes](../patches/README.md) |
| `tes3x_docs.py` | check the documentation's links and regenerate its index | [documentation index](index.md) |

## Disk images

`tes3x_fatx.py`, `tes3x_put.py` and `tes3x_qcow2.py` create, fill and convert raw and qcow2 Xbox
hard-disk images for xemu; the xemu runner and the save tools use them. The other scripts in `tools/` are libraries the
commands above share.
