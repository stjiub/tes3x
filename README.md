# TES3X

TES3X is a patching toolkit for *Morrowind Game of the Year Edition* on the original Xbox. It
applies engine fixes to the retail XBE, and can also assemble and deploy a complete modded game
folder.

## Requirements

- Python 3.12 or newer and Pillow (`python -m pip install Pillow`)
- A clean retail game folder containing `Default.xbe`, `morrowind.xbe`, `Morrowind.ini` and
  `Data Files`
- [LLVM](https://releases.llvm.org) for engine fixes and `delta-bsa` packaging; set `paths.llvm`
  if `clang` and `lld-link` are not on `PATH`
- A softmodded or hardmodded Xbox, or xemu
- An FTP server running on the Xbox (only if using `--deploy`)

## Setup

Copy the example TOML files to the repository root.

```
cp examples/local.toml tes3x.local.toml
cp examples/profile.toml profile.toml
```

In `tes3x.local.toml`, set `vanilla_root` to your unmodified Morrowind GOTY Xbox files and, if
needed, add the LLVM and Xbox connection settings.
In `profile.toml`, set the build name and select its mods and patches.
Use a separate profile file for each build.

The mod library contains one directory per mod, with the layout each mod would use under
`Data Files`. Wrapper directories from extracted archives are detected automatically.

The same profile format covers every build:

- For engine fixes only, remove `library` and every `[[mods]]` block.
- For mods without optional engine fixes, set `preset = "minimal"`.
- For both, keep the mod list and use `standard` or `development`.

## Build

```
python tools/tes3x_pipeline.py profile.toml --check
python tools/tes3x_pipeline.py profile.toml
```

`--check` validates and resolves the profile without building. A normal build is written to
`build/pipeline/<name>/deploy`. Add `--dry-run` to preview an FTP sync or `--deploy` to apply it.

## Deploy

`--deploy` synchronizes the build to `remote_root`; files there that are not in the build are
deleted. Use a separate destination, not an existing install you want to preserve. Saves and the
dashboard's `_resources` directory are not touched.

Connection settings come from command-line options, then `[deploy]` in `tes3x.local.toml`, then
the default `xbox`/`xbox` login. Set `TES3X_FTP_PASSWORD` or use `--ask-password` to keep the
password out of the config file.

You can also copy `build/pipeline/<name>/deploy` with another FTP client.

## Choosing engine fixes

The profile's `preset` selects a baseline:

| preset | contents |
|---|---|
| `minimal` | No optional engine fixes |
| `standard` | Tested engine fixes |
| `development` | `standard`, diagnostics and the in-game console |

Use `enable` and `disable` for individual patches. See [available patches](docs/patches.md),
[candidates](docs/candidates.md), or run:

```powershell
python tools/tes3x_patch.py --list
```

## In-game console

With `console` enabled, Back + right thumb click opens the console and A raises the on-screen
keyboard. `[Xbox] ConsoleCombo` changes the combination.

To run commands without typing, put a `tes3xexec.txt` in `E:\` or next to `default.xbe`; `E:\`
is checked first. Each launch runs its lines once, one per frame:

```
# lines starting with @menu run while the main menu is up
@menu click MenuOptions MenuOptions_New_container
wait 30
player->getpos x
coc "Balmora"
```

Other lines start once the game is running. `wait N` pauses for N frames, `click MENU WIDGET`
presses a menu widget, and `exit` turns the Xbox off. Every command and the first lines of its
output are recorded in `E:\tes3xlog.txt`. A file in `E:\` runs on every launch until you delete it.

## Packaging and overrides

`delta-bsa` keeps mod assets in a separate archive for smaller rebuilds and uploads, but requires
LLVM. `merged-bsa` needs no compiler and rebuilds `Morrowind.bsa`.

The annotated [example profile](examples/profile.toml) covers the common options. Command-line
values override it:

```
python tools/tes3x_pipeline.py profile.toml --preset development --enable console
python tools/tes3x_pipeline.py profile.toml --ini-set "General:Show FPS=1"
```

Set `hardlink_retail = true` in the local config to avoid duplicating unchanged retail files when
the source and build are on the same NTFS volume. See the
[configuration reference](docs/configuration.md) for every TOML option and
[pipeline options](docs/pipeline.md) for advanced workflows.

## Other tools

The pipeline uses these commands internally; they can also be run directly:

| tool | use |
|---|---|
| `tes3x_patch.py` | Patch an XBE directly. `--list` shows patches. |
| `tes3x_build.py` | Collect mods only, and report conflicts, texture sizes and missing masters. |
| `tes3x_pack.py` | Pack a collected mod tree into a game folder. |
| `tes3x_deploy.py` | Upload a game folder to the Xbox. |
| `tes3x_fetch.py` | Copy files back off the Xbox, such as saves or logs. |
| `tes3x_audit.py` | Check any `Data Files` folder for long names, junk files and duplicates. |
