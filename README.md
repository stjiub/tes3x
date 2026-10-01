# TES3X

TES3X is a modding and patching toolkit for *Morrowind Game of the Year Edition* on the original
Xbox. It applies engine fixes to the retail XBE, extends what the engine can do, and assembles,
optimizes and deploys a list of mods directly to an Xbox.

## Features

- **Engine fixes.** Bugs from the [Morrowind Code Patch](https://www.nexusmods.com/morrowind/mods/19510)
  and other projects, rewritten for the Xbox build, and fixes for problems only the Xbox has.
  Each is chosen by preset or by name, and located in the XBE by content, so a build fails
  loudly rather than patching the wrong place.
- **Engine extensions.** Several BSA archives, new script opcodes, legacy MWSE bytecode, more
  memory on 128 MB consoles, rotating and transition autosaves, an in-game console, and
  experimental multiplayer.
- **A mod pipeline.** One profile turns a mod library into a complete game folder: the winning
  files collected, textures shrunk to a budget, file names checked against FATX's limits, plugins
  ordered, assets packed into archives and `Morrowind.ini` settings applied.
- **Deployment.** Builds upload over FTP to a folder of their own, sending only what changed,
  stamping plugin load order and clearing the game's cache partitions.
- **A GUI** for the mod library, profiles, plugins, patches and INI settings, with each mod's
  known Xbox compatibility.
- **Testing and diagnostics.** Builds run in xemu from scripts; patches have scripted game tests;
  crash records, a hang watchdog, a function profiler and memory censuses work on the console.

## Status and limitations

TES3X is experimental. It is developed against the retail GOTY release and tested in xemu and on
original hardware, but each patch matures at its own pace: the [patch table](docs/patches.md)
gives every patch's channel, from `dev` to `release`, and the
[candidates](docs/candidates.md) list what has been looked at and not implemented.

- The Xbox has 64 MB of memory, and a stock console cannot load everything the PC can. The
  largest mods, such as the complete Tamriel Rebuilt, need a console upgraded to 128 MB.
- Engine fixes are compiled for your own XBE when you build, so they need LLVM.
- The [compatibility catalog](docs/catalog.md) lists mods tried on the Xbox and how they fared;
  many PC mods depend on features the Xbox build lacks.
- TES3X includes no game files. You supply your own copy of the game.

## Quick start

You need Python 3.12 or newer with Pillow, a clean copy of the game's Xbox files, and for engine
fixes [LLVM](https://releases.llvm.org). With the GUI:

```powershell
python -m pip install Pillow -r requirements-gui.txt
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

## Documentation

The [documentation index](docs/index.md) lists every page. The main ones:

- [Getting started](docs/getting-started.md): from a clean game to a build on the Xbox.
- [GUI](docs/gui.md), [mod library](docs/mod-library.md) and [pipeline](docs/pipeline.md).
- [Configuration](docs/configuration.md): every profile and local-config key.
- [Patches](docs/patches.md), [candidates](docs/candidates.md),
  [ini keys](docs/ini-keys.md) and [patch policy](docs/patch-policy.md).
- [Compatibility catalog](docs/catalog.md).
- [Testing](docs/testing.md), [xemu](docs/xemu.md), [exec scripts](docs/exec-scripts.md),
  [game tests](docs/game-tests.md), [validation](docs/validation.md) and
  [diagnostics](docs/diagnostics.md).
- [Multiplayer](docs/multiplayer.md), [add-ons](docs/addons.md) and the
  [symbol map](docs/symbol-map.md).

## Credits

Ideas and data from other projects, reimplemented rather than copied:

- [Morrowind Code Patch](https://www.nexusmods.com/morrowind/mods/19510), whose catalogue of engine
  bugs and their fixes is the source of most ports.
- [MWSE](https://mwse.github.io/MWSE/), [OpenMW](https://openmw.org),
  [MGE XE](https://www.nexusmods.com/morrowind/mods/41102) and the
  [Skill Increase GMST Fix](https://www.nexusmods.com/morrowind/mods/48029), for fixes and
  features; the patch and candidate tables credit each one.
- [mlox](https://github.com/mlox/mlox) and the
  [mlox-rules](https://github.com/DanaePlays/mlox-rules) project, for plugin ordering. Neither is
  included; TES3X downloads the rules on request.

Bundled third-party files:

- [Monocypher](https://monocypher.org), the multiplayer session's cryptography
  (`hooks/monocypher.c`, `hooks/monocypher.h`), under BSD-2-Clause or CC0-1.0.
- The Xbox kernel export names from [nxdk](https://github.com/XboxDev/nxdk)
  (`hooks/xboxkrnl.exe.def`), under CC0-1.0.

Tools used but not included: [xemu](https://xemu.app),
[extract-xiso](https://github.com/XboxDev/extract-xiso), [LLVM](https://llvm.org),
[7-Zip](https://www.7-zip.org), and the Python packages Pillow, PySide6 and tomlkit.

*The Elder Scrolls III: Morrowind* is a trademark of Bethesda Softworks. TES3X is not affiliated
with or endorsed by Bethesda or ZeniMax.

## License

TES3X is licensed under the [GNU General Public License v3.0](LICENSE). The bundled files listed
under Credits keep their own licenses.
