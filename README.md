# ![TES3X](assets/logo.png)

TES3X is a modding and patching toolkit for the original Xbox release of *Morrowind Game of the
Year Edition*. It applies engine fixes to the retail XBE, extends the engine, and manages,
optimizes and deploys a mod list. It also adds multiplayer.

## Features

- **Engine fixes.** Performance and stability improvements, bug fixes ported from the
  [Morrowind Code Patch](https://www.nexusmods.com/morrowind/mods/19510) and similar projects,
  and Xbox-specific patches.
- **Engine extensions.** New script opcodes, legacy MWSE (0.9.x) support, multi-BSA loading, an
  in-game console, diagnostics and profiling, 128 MB RAM and 720p video, PC mod compatibility
  patches, and multiplayer.
- **Mod management.** A profile-based mod library with the usual management features, plus what
  the Xbox needs: texture shrinking, fixes for FATX's limits, and building and deploying to an
  Xbox over FTP or to xemu.
- **Mod compatibility.** A growing list of mods tested on the Xbox, and patches for some that
  don't work as they are.
- **Modularity.** TES3X makes many changes, but each piece is optional rather than
  all-or-nothing.

## Status and limitations

- TES3X is under active development and changes often, sometimes in breaking ways.
- It is developed against the retail **GOTY** NTSC release and tested in both xemu and on
  original hardware. PAL is not supported yet, but is planned.
- The original Xbox has 64 MB of memory, and a stock console cannot load everything the PC can.
  The largest mods, such as the complete Tamriel Rebuilt, need a console upgraded to 128 MB.
- Expect crashes while testing new mods and pushing the limits of 25-year-old hardware.
- TES3X includes no game files. You supply your own copy of the game, and engine fixes are built
  from your own XBE.
- The [compatibility catalog](docs/catalog.md) lists mods tried on the Xbox and how they fared;
  many PC mods depend on features the Xbox build lacks.

## Quick start

- **Windows:** download `TES3X-<version>.zip` from the
  [releases page](https://github.com/stjiub/tes3x/releases), unpack it and run `TES3X.exe`.
  Nothing else needs installing.
- **With Python 3.12 or newer, on Windows or Linux:**
  `pipx install "tes3x[gui] @ git+https://github.com/stjiub/tes3x"`, then `tes3x gui`.

[Getting started](docs/getting-started.md) covers both, what else you need and a first build.

## Docs

- [Getting started](docs/getting-started.md)
- [GUI](docs/gui.md)
- [Pipeline](docs/pipeline.md)
- [Patches](docs/patches.md)
- [All documentation](docs/index.md)
- [Development](docs/development.md)

## License

[GPL-3.0-or-later](LICENSE): the GNU General Public License, version 3 or any later version.
