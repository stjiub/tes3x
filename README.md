# ![TES3X](assets/logo.png)

TES3X is a modding and patching toolkit for the original-Xbox release of *Morrowind Game of the
Year Edition*, played on original hardware or in [XEMU](). It applies engine fixes to the retail XBE,
extends engine capabilites, and manages, optimizes and deploys a modlist. It attempts to both improve
and push the limits of the Morrowind experience on the Xbox. 

## Features
- **Engine fixes.** - Performance and stability improvements, porting bug fixes from the
  [Morrowind Code Patch](https://www.nexusmods.com/morrowind/mods/19510)
  and similar projects, and Xbox specific patches.
- **Engine extensions.** New script opcodes, support for legacy MWSE (0.9.X),
  multi-BSA loading, in-game console, diagnostics/profiling, 128MB RAM support,
  PC mod compatibility patches, and co-op multiplayer. ([Patches](docs/patches.md))
- **Mod management/pipeline.** Profile based mod library with the usual management features,
  plus those specific to the Xbox: texture shrinking, optimizations for FATX's limitations, building and
  deployment to Xbox (via FTP) or XEMU.
- **Mod compatibility.** A growing list of tested mods that are compatible as well as patches for
  some that aren't.
- **Modularity.** TES3X contains many components and makes many changes, but strives to make each piece an optional component vs an all-or-nothing approach.

## Status and limitations

- TES3X is under active development and is subject to frequent (possibly breaking) changes. 
- It is developed against the retail **GOTY** release and tested in both XEMU and
original hardware, but each patch matures at its own pace.
- The original Xbox only has 64 MB of memory, and a stock console cannot load everything the PC can. The
  largest mods, such as the complete Tamriel Rebuilt, need a console upgraded to 128 MB.
- We aim for stability and performance, but expect crashes when you are testing new mods and pushing the
  limits of 25+ year old hardware.
- Engine fixes are compiled for your own XBE when you build, so they need LLVM.
- TES3X includes no game files. You supply your own copy of the game.
- The [compatibility catalog](docs/catalog.md) lists mods tried on the Xbox and how they fared;
  many PC mods depend on features the Xbox build lacks.


## Quick start

You need a Windows PC, a clean copy of the game's Xbox files, Python 3.12 or newer with the
packages in `requirements-gui.txt`, and for engine fixes [LLVM](https://releases.llvm.org). With
the GUI:

```powershell
python -m pip install -r requirements-gui.txt
python tools/tes3x_gui.py
```

From the command line:

```powershell
cp examples/local.toml tes3x.local.toml
mkdir profiles
cp examples/profile.toml profiles/my-build.toml
# edit both files, then:
python tools/tes3x_pipeline.py profiles/my-build.toml --check
python tools/tes3x_pipeline.py profiles/my-build.toml --dry-run
python tools/tes3x_pipeline.py profiles/my-build.toml --deploy
```

`--deploy` makes the Xbox folder match the build and deletes anything else in it, so give each
build a folder of its own. [Getting started](docs/getting-started.md) walks through every step.

## Docs

- [Getting Started](docs/getting-started.md)
- [Patches](docs/patches.md)
- [Candidates](docs/candidates.md)
- [Pipeline](docs/pipeline.md)
- [Full TES3X Documentation](docs/index.md)

## License

[GPL-3.0](LICENSE).
