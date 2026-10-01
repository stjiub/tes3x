# TES3X

TES3X is a modding and patching toolkit for *Morrowind Game of the Year Edition* on the original
Xbox. It applies engine fixes to the retail XBE, extends what the engine can do, and assembles,
optimizes and deploys a list of mods directly to an Xbox.

## Features

- Engine fixes and extensions applied to the retail XBE
- A mod pipeline: collect, convert, pack and order mods into a complete game folder
- Deployment to the Xbox over FTP
- A GUI for mods, profiles, patches and settings
- Testing in xemu, and diagnostics on the console

## Status

TES3X is experimental and changes often. It comes with no guarantees: keep backups of your saves
and your game, and expect builds to break between versions. Each patch's
[channel](docs/patches.md) says how far it has been tested.

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

## License

[GPL-3.0](LICENSE).
