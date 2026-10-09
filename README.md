# ![TES3X](assets/logo.png)

TES3X is a modding and patching toolkit for the original Xbox release of *Morrowind Game of the
Year Edition*. It applies engine fixes to the retail XBE, extends engine capabilites, and manages, 
optimizes and deploys a modlist. It also includes support for multiplayer.

## Features
- **Engine fixes.** - Performance and stability improvements, porting bug fixes from the
  [Morrowind Code Patch](https://www.nexusmods.com/morrowind/mods/19510)
  and similar projects, and Xbox specific patches.
- **Engine extensions.** New script opcodes, support for legacy MWSE (0.9.X),
  multi-BSA loading, in-game console, diagnostics/profiling, 128MB RAM and 720p video support,
  PC mod compatibility patches, and multiplayer. 
- **Mod management/pipeline.** Profile based mod library with the usual management features,
  plus those specific to the Xbox: texture shrinking, optimizations for FATX's limitations, building and
  deployment to Xbox (via FTP) or XEMU.
- **Mod compatibility.** A growing list of tested mods that are compatible as well as patches for
  some that aren't.
- **Modularity.** TES3X contains many components and makes many changes, but strives to make each piece an optional component vs an all-or-nothing approach.

## Status and limitations

- TES3X is under active development and is subject to frequent (possibly breaking) changes. 
- It is developed against the retail **GOTY** NTSC release and tested in both XEMU and
original hardware. PAL is not currently supported, but is planned.
- The original Xbox only has 64 MB of memory, and a stock console cannot load everything the PC can. The
  largest mods, such as the complete Tamriel Rebuilt, require a console upgraded to 128 MB.
- Expect crashes when you are testing new mods and pushing the limits of 25+ year old hardware.
- TES3X includes no game files. You supply your own copy of the game.
- Engine fixes are compiled using your own XBE when you build.
- The [compatibility catalog](docs/catalog.md) lists mods tried on the Xbox and how they fared;
  many PC mods depend on features the Xbox build lacks.

## Docs

- [Getting Started](docs/getting-started.md)
- [Patches](docs/patches.md)
- [Candidates](docs/candidates.md)
- [Pipeline](docs/pipeline.md)
- [Full TES3X Documentation](docs/index.md)

## License

[GPL-3.0-or-later](LICENSE): the GNU General Public License, version 3 or any later version.
