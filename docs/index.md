# TES3X documentation

Generated from [`nav.toml`](nav.toml) by `tools/tes3x_docs.py --write`; edit that file, not this one.

## Start here

- [Getting started](getting-started.md): This page takes you from a clean copy of the game to a modded build running on your Xbox.

## Guides

- [GUI](gui.md): The GUI edits everything a profile and the local config hold, installs mods into the library, and checks, builds, tests and deploys.
- [Mod library](mod-library.md): The mod library is a folder of mods, one folder each, laid out the way each mod would sit in `Data Files`.
- [Pipeline](pipeline.md): `tools/tes3x_pipeline.py` turns one profile into a complete game folder, and optionally deploys it.
- [Packaging](packaging.md): The pipeline's packing stage turns the collected mod files and the retail `Data Files` into what ships to the Xbox.
- [Deployment](deployment.md): `--deploy` uploads a finished build to the Xbox over FTP with `tools/tes3x_deploy.py`.
- [Testing](testing.md): TES3X is tested in steps, from fast checks on the PC to play on an original Xbox.
- [Running in xemu](xemu.md): Set up xemu to play profiles or run them from scripts.
- [Patch game tests and validation](validation.md): Each patch can have a repeatable in-game test in `tests/game/PATCH.toml`.
- [Diagnostics and profiling](diagnostics.md): A patched build can record what it does, time engine functions and count where memory goes.
- [Multiplayer](multiplayer.md): Run a TES3X server and connect consoles and xemu to it.
- [Add-ons](addons.md): Add-ons are optional parts of TES3X for particular setups.

## Reference

- [Configuration reference](configuration.md): Every key in a profile and in the local config.
- [Commands](commands.md): Every TES3X command is a Python script in `tools/`, run from the repository root.
- [Exec scripts](exec-scripts.md): With the console patch, a build runs console commands from a script file once per launch, with no controller.
- [Game tests](game-tests.md): A game test is a TOML file in `tests/game/`, named after the patch it tests: `mcp-37.toml` tests `mcp-37`.
- [Ini keys](ini-keys.md): Keys TES3X patches read from the `[Xbox]` section of `Morrowind.ini`.
- [Patches](patches.md): Every patch TES3X can apply, with its channel and how a build selects it.
- [Candidate fixes](candidates.md): Fixes from other projects that were reviewed, and why each is not implemented.
- [Mod compatibility](catalog.md): Mods tried on the Xbox, and whether they work.

## Development

- [Patch policy](patch-policy.md): Which engine fixes TES3X takes on and what each one defaults to.
- [Patch notes](../patches/README.md): What a patch's notes page contains, and its template.
- [Symbol map](symbol-map.md): `symbols/curated.json` names functions and code sites in the retail Xbox `morrowind.xbe`, so that an address in a crash log, a profile or a disassembly means something.
