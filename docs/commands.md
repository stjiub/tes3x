# Commands

Every TES3X command is a subcommand of `tes3x`: `tes3x COMMAND [ARGS]`, or
`python -m tes3x COMMAND [ARGS]`. `tes3x --help` lists them; `--help` after a command lists its
options. This page says what each one is for and where it is explained.

## Building and deploying

| command | job | see |
|---|---|---|
| `tes3x gui` | the GUI: profiles, mods, plugins, patches, INI, build, play and deploy | [GUI](gui.md) |
| `tes3x init` | copy the bundled example config and profile into the data folder, or a chosen folder; keep existing files | [getting started](getting-started.md#where-tes3x-keeps-your-files) |
| `tes3x pipeline` | build one profile into a game folder, and optionally deploy it | [pipeline](pipeline.md) |
| `tes3x build` | collect a profile's mods into one `Data Files` tree; report conflicts, textures and missing masters; prune unused assets | [pipeline](pipeline.md#2-collect-the-winning-files), [packaging](packaging.md#pruning-unused-assets) |
| `tes3x plugins` | order plugins with mlox or a saved order; download the mlox rules; run TES3Merge | [pipeline](pipeline.md#sorting-plugins-with-mlox) |
| `tes3x optimize` | write equivalence-checked derived copies that make the Xbox load fewer records | [plugin optimizer](optimizer.md) |
| `tes3x pack` | pack a collected tree into archives and stage the game's `Data Files` | [packaging](packaging.md) |
| `tes3x payload` | compile the patch payload for one retail XBE | [payload](../patches/payload.md) |
| `tes3x nxdk` | build a standalone nxdk program, such as the console-side manager | [configuration](configuration.md) |
| `tes3x font` | bake the console manager's fonts into C, from pinned OFL fonts | [development](development.md#generated-files) |
| `tes3x patch` | apply patches to an XBE directly; `--list` shows them | [patches](patches.md) |
| `tes3x deploy` | upload a game folder to the Xbox, over FTP or through the console manager | [deployment](deployment.md) |
| `tes3x manager` | install the console manager on the Xbox, or send it a signed update | [deployment](deployment.md#installing-the-console-manager) |
| `tes3x manifest` | check a game folder against its build manifest | [deployment](deployment.md#build-manifest) |
| `tes3x fetch` | copy files or folders off the Xbox | [deployment](deployment.md#getting-files-back) |
| `tes3x saves` | list and move saves between the Xbox, an xemu disk and a PC save library | [GUI](gui.md) |
| `tes3x package` | build a portable TES3X folder that starts with `TES3X.exe` | [development](development.md#building-the-portable-folder) |
| `tes3x release` | make and protect the release signing key, sign and check files, and make or check a signed manager release | [development](development.md#releases) |

## Mods

| command | job | see |
|---|---|---|
| `tes3x library` | check, scan or convert a versioned mod library | [mod library](mod-library.md) |
| `tes3x nexus` | look a mod up on Nexus Mods | [GUI](gui.md) |
| `tes3x audit` | check a `Data Files` folder for long names, junk files and duplicates | [packaging](packaging.md#sound-and-mesh-checks) |
| `tes3x assets` | check NIF texture references and WAV formats; resample WAVs | [packaging](packaging.md#sound-and-mesh-checks) |
| `tes3x mwse` | find legacy MWSE bytecode in compiled plugins | [legacy MWSE bytecode](../patches/mwse-legacy.md) |
| `tes3x map` | inspect a map companion file such as `Morrowind.esm.map` | [packaging](packaging.md#sound-and-mesh-checks) |
| `tes3x bsa` | report the entry count and content size of an Xbox BSA archive | |
| `tes3x convert` | preview texture conversion sizes without writing files | [packaging](packaging.md) |

## Testing

| command | job | see |
|---|---|---|
| `tes3x xemu` | build and run a profile in xemu, interactively or from an exec script | [xemu](xemu.md) |
| `tes3x xemu-setup` | find xemu's files in one folder, or download xemu and a blank disk | [xemu](xemu.md) |
| `tes3x test` | smoke-test a profile in xemu, or every library mod on its own | [testing](testing.md#profile-smoke-tests) |
| `tes3x scenario` | run a patch's game test and apply its verdict | [validation](validation.md) |
| `tes3x validate` | record and check local game-test results | [validation](validation.md) |
| `tes3x tour` | write a script that tours a plugin's cells logging memory; summarise the log | [diagnostics](diagnostics.md#memory-tours) |
| `tes3x orphan` | write a test plugin with references that cannot resolve | [unresolvable reference cleanup](../patches/mcp-1.md) |
| `tes3x scriptasm` | write a plugin that calls a new script opcode | [script opcode extensions](../patches/script-ext.md) |

## Diagnostics

| command | job | see |
|---|---|---|
| `tes3x diag` | fetch and summarise the diagnostics log | [diagnostics](diagnostics.md#the-log) |
| `tes3x prof` | pick profiler targets by name; render the profiler dump | [diagnostics](diagnostics.md#profiling) |
| `tes3x heap` | render the engine heap census | [diagnostics](diagnostics.md#heap-census) |
| `tes3x mem` | render the kernel memory census | [diagnostics](diagnostics.md#memory-census) |
| `tes3x readlog` | read the log or another file from an xemu disk image | [xemu](xemu.md) |
| `tes3x ess` | break a save into record types, changed references and inventories; `--diff` compares two saves by kind of state | [character restore diagnostics](multiplayer-server.md#character-restore-diagnostics) |

## Multiplayer

| command | job | see |
|---|---|---|
| `tes3x net` | run a multiplayer server, administer it, and test the network driver | [multiplayer](multiplayer.md) |
| `tes3x agent` | listen for the in-game agent; run console lines, fetch files or reboot through it, and through the console manager write, list, rename and delete files, report free space and launch an XBE | [in-game agent](../patches/agent.md) |
| `tes3x menuart` | redraw the menu buttons multiplayer ships, in `assets/menu` (needs Pillow and numpy) | [development](development.md#generated-files) |

## Reverse engineering

| command | job | see |
|---|---|---|
| `tes3x sym` | build and query the XBE's symbol map; decompile through Ghidra | [symbol map](symbol-map.md) |
| `tes3x layouts` | compile MWSE's struct layouts into data types for Ghidra | [symbol map](symbol-map.md#types) |
| `tes3x xbe` | print an XBE's header, certificate, sections and strings | |
| `tes3x disasm` | disassemble an address range, naming calls from the symbol map, and find references and direct calls to an address | |
| `tes3x inject` | add a section to an XBE and retarget its entry point | [payload](../patches/payload.md) |
| `tes3x ini` | list every `Morrowind.ini` key the XBE reads, with its default | [ini keys](ini-keys.md) |
| `tes3x mcp` | read a Morrowind Code Patch fix against the original PC executable | [patch policy](patch-policy.md) |
| `tes3x patches` | check the patch table and regenerate its pages | [development](development.md#generated-files) |
| `tes3x docs` | check the documentation's links and regenerate its index | [development](development.md#generated-files) |

## Disk images

| command | job | see |
|---|---|---|
| `tes3x fatx` | create and inspect FATX partitions | [xemu](xemu.md) |
| `tes3x put` | write files into an Xbox hard-disk image | [xemu](xemu.md) |
| `tes3x qcow2` | inspect a qcow2 image, or convert it to raw with an output path | [xemu](xemu.md) |
