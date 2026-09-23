# TES3X

TES3X builds modded *Morrowind Game of the Year Edition* for the original Xbox. You give it your
own copy of the game, a folder of mods and a short list of what you want. It produces a complete
game folder ready to copy to the Xbox, with the mods packed in the form the Xbox needs and
`morrowind.xbe` patched with any engine fixes you chose.

It can also install that folder on the Xbox over FTP.

The project is young. Commands and file formats may still change.

## How it works

You write two small text files:

- a **profile** describes one build: which mods, which engine fixes, what to call it. You can
  keep several profiles, for example one per mod setup.
- a **local config**, `tes3x.local.toml`, says where things are on your PC and on your network.
  You write it once.

Then one command, `tools/tes3x_pipeline.py`, does the work:

1. **Collect mods.** Copies every file from each mod folder you listed. When two mods contain the
   same file, the one with the higher `order` wins. Textures that are too large are shrunk, and
   file names are checked against the Xbox's 42-character limit.
2. **Pack.** Puts mod meshes and textures into a BSA archive, the format the game reads fastest.
3. **Patch.** Copies your `morrowind.xbe` and patches the copy. Every build gets two patches: one
   that lets the game run from the hard drive, and one that makes it read its files from its own
   folder. Any engine fixes you chose are added on top. Your original XBE is never changed.
4. **Stage.** Puts it all together as a complete game folder in `build/pipeline/<profile name>/deploy`.
5. **Deploy** (optional). Uploads that folder to the Xbox.

If any step fails, the previous build is left untouched.

## What you need

- **Python 3.12 or newer**, and Pillow: `python -m pip install Pillow`
- **Your own clean copy of the game folder**: `Default.xbe`, `morrowind.xbe`, `Morrowind.ini` and
  `Data Files`, not already modded.
- **A softmodded Xbox with an FTP server**, if you want TES3X to upload builds. Otherwise copy the
  output folder with any FTP client.
- **[LLVM](https://releases.llvm.org)**, only for engine fixes and for the `delta-bsa` packing
  mode. Some fixes include a small piece of compiled code that is built to fit your XBE, and LLVM
  supplies the compiler (`clang`) and linker (`lld-link`). On Windows, run the LLVM installer and
  let it add LLVM to your PATH, or set `llvm` in the local config to its `bin` folder.

  Without it, you can still build mods using `preset = "minimal"` and `mode = "merged-bsa"`.

## First-time setup

Run every command from this folder (the one containing this README).

1. Copy `examples/local.toml` to `tes3x.local.toml` and fill it in:

   ```toml
   [paths]
   vanilla_root = "C:/Games/Morrowind-clean"   # your clean game folder
   build_root = "build/pipeline"
   # llvm = "C:/Program Files/LLVM/bin"       # only if clang is not on PATH
   # hardlink_retail = true                   # see "Saving disk space" below

   [deploy]
   host = "192.168.1.50"                      # the Xbox's IP address
   remote_root = "F:/Games/MorrowindModded"   # where the build goes on the Xbox
   # user = "xbox"                            # FTP login, if not the default xbox/xbox
   # password = "xbox"
   ```

   Use forward slashes in paths.

2. If you use mods, put them in a **mod library**: one folder that contains one folder per mod.
   Each mod folder holds what would go in `Data Files`, such as `meshes`, `textures` and `.esp`
   files. An extra wrapper folder from unzipping is fine.

   ```text
   D:/Morrowind Mods/
     Better Bodies/
       meshes/  textures/  Better Bodies.esp
     Atlas/
       Atlas/            <- wrapper folder, found automatically
         meshes/  textures/
   ```

## Recipes

Each recipe is: copy an example profile, edit it, then run the same three commands.

### A. Mods only

Copy `examples/mods.toml` to, for example, `my-mods.toml`. Set `library`, list your mods, and set
`preset = "minimal"` so no engine fixes are added:

```toml
[profile]
name = "my-mods"
library = "D:/Morrowind Mods"

[patches]
preset = "minimal"

[package]
mode = "merged-bsa"     # or "delta-bsa" if the compiler is set up

[[mods]]
name = "Better Bodies"
order = 10

[[mods]]
name = "Atlas"
order = 20
```

### B. Engine fixes only

Copy `examples/patches-only.toml`. A profile with no `[[mods]]` keeps the game's own files
unchanged and only patches `morrowind.xbe`:

```toml
[profile]
name = "patched"

[patches]
preset = "standard"
enable = ["console"]    # add any extra patches by name
```

### C. Mods and engine fixes

Use `examples/mods.toml` as it is: list your mods and keep `preset = "standard"`. This is A and B
together.

### Then build it

```powershell
python tools/tes3x_pipeline.py my-mods.toml --plan
python tools/tes3x_pipeline.py my-mods.toml
python tools/tes3x_pipeline.py my-mods.toml --dry-run
python tools/tes3x_pipeline.py my-mods.toml --deploy
```

| command | what it does |
|---|---|
| `--plan` | Prints which patches and how many mods would be used, and where the output goes, then stops. Nothing is built. Use it to check a profile. |
| *(no option)* | Builds. The finished game folder is `build/pipeline/<name>/deploy`. Nothing is sent to the Xbox. |
| `--dry-run` | Builds, connects to the Xbox and lists which files `--deploy` would upload and delete. Changes nothing on the Xbox. |
| `--deploy` | Builds, then uploads the changes to the Xbox. |

## Putting a build on the Xbox

With `--deploy`, TES3X makes the Xbox folder match the build. The folder is the profile's
`remote_root`, or the local config's `remote_root` if the profile has none, so each profile can
have its own install:

- **Anything in that folder that is not part of the build is deleted.** Point `remote_root` at a
  new folder, not at an install you want to keep. Saves are stored elsewhere and are not touched,
  and neither is the dashboard's `_resources` folder (its name, artwork and screenshots).
- The first upload sends everything and can take a long time over a slow network. Later uploads
  send only files that changed.
- It clears the Xbox's cache partitions when `clear_cache_partitions = true`, so the game does not
  keep using old copies of changed files.

**FTP login.** Every tool that talks to the Xbox (`--deploy`, `tes3x_deploy.py`,
`tes3x_fetch.py`, `tes3x_diag.py pull`) finds the address and login the same way: the command
line (`--host`, `--user`, `--password`) first, then the `[deploy]` section of `tes3x.local.toml`,
then the dashboard default `xbox`/`xbox`. To keep the password out of files, set the
`TES3X_FTP_PASSWORD` environment variable, or add `--ask-password` to be asked each time.

To copy by hand instead, upload the contents of `build/pipeline/<name>/deploy` to a folder on the
Xbox with any FTP client.

For a patches-only build, the only files that differ from the retail game are `morrowind.xbe` and,
if you set `[ini]` keys, `Morrowind.ini`. You can copy just those over an existing install. Keep a
backup of the originals.

## Choosing engine fixes

`preset` sets the starting point:

| preset | contents |
|---|---|
| `minimal` | No engine fixes. Only the two patches every build needs. |
| `standard` | Engine fixes that are tested and safe to leave on. |
| `development` | `standard` plus crash logging and the in-game console. |

Then add or remove single patches by name with `enable` and `disable`.
[docs/patches.md](docs/patches.md) lists every patch, what it does and which preset includes it.
Fixes that are not available yet, and why, are in [docs/candidates.md](docs/candidates.md).
The same list is printed by:

```powershell
python tools/tes3x_patch.py --list
```

`--plan` prints the final list, including patches added automatically. For example, `delta-bsa`
adds `multi-bsa`, which lets the game load the extra archive.

## Profile reference

```toml
[profile]
name = "my-mods"            # required; also the output folder name
title = "Morrowind Modded"  # optional; the name shown in the Xbox dashboard (see below)
remote_root = "F:/Games/MorrowindModded"  # optional; overrides the local config's folder
library = "D:/Morrowind Mods"    # required when there are mods

[rules]
max_texture_size = 512      # longest texture side; larger ones are shrunk
clear_cache_partitions = true

[patches]
preset = "standard"
enable = []
disable = []

[package]
mode = "delta-bsa"          # or "merged-bsa"
archive_only = false        # true: the game never looks for loose files. Faster, but loose overrides stop working

[ini]                       # optional Morrowind.ini changes
"General:Show FPS" = 1

[[mods]]
name = "Better Bodies"      # folder name in the library
order = 10                  # higher wins when two mods contain the same file
enabled = true              # false leaves it out
optional = false            # true: skip quietly if the folder is missing
plugins = ["Better Bodies.esp"]  # optional: ship only these plugins
```

**Packing modes.** `delta-bsa` puts mod assets in their own archive beside the untouched
`Morrowind.bsa`, so rebuilds and uploads are small; it needs the compiler setup. `merged-bsa`
rebuilds `Morrowind.bsa` with the mods inside it; it needs no compiler, but the whole archive is
uploaded again after every change.

**Dashboard name.** With `title` set, the build renames both XBEs, which is the name any
dashboard can fall back to. Some dashboards read the name from their own file instead; the
profile's `dashboards` list says which of those files to write. It defaults to
`["xbmc4gamers"]`, which writes `_resources/default.xml`; set `dashboards = []` to write none.
Dashboards cache names when they scan, so rescan the games list after deploying.

**Load order.** Morrowind on the Xbox loads plugins in file-date order. TES3X stamps each plugin's
date so masters load before the plugins that need them.

A missing mod folder stops the build, so a typo cannot quietly remove a mod from the Xbox.

## Saving disk space

Every build contains a full copy of the unchanged retail files, about 1 GB for a patches-only
build. With `hardlink_retail = true` in the local config, or `--hardlink` for one run, those files
are hardlinked instead: they appear in the build but take no extra space. This works only when the
build folder and the retail folder are on the same NTFS drive; elsewhere TES3X quietly copies.

## Command-line overrides

Most profile settings can be overridden for a single run:

```powershell
python tools/tes3x_pipeline.py my-mods.toml --preset development --enable console
python tools/tes3x_pipeline.py my-mods.toml --ini-set "General:Show FPS=1"
python tools/tes3x_pipeline.py my-mods.toml --out D:/builds/test
```

`python tools/tes3x_pipeline.py --help` lists them all.

## Other tools

The pipeline runs these for you. They can also be used on their own:

| tool | use |
|---|---|
| `tes3x_patch.py` | Patch an XBE directly. `--list` shows patches. |
| `tes3x_build.py` | Collect mods only, and report conflicts, texture sizes and missing masters. |
| `tes3x_pack.py` | Pack a collected mod tree into a game folder. |
| `tes3x_deploy.py` | Upload a game folder to the Xbox. |
| `tes3x_fetch.py` | Copy files back off the Xbox, such as saves or logs. |
| `tes3x_audit.py` | Check any `Data Files` folder for long names, junk files and duplicates. |

Copy saves off the Xbox:

```powershell
python tools/tes3x_fetch.py "E:/UDATA/42530005" --host 192.168.1.50 --out build/saves
```

The Xbox FTP server cannot open the `T:` and `U:` drives; their contents are under
`E:/TDATA/42530005` and `E:/UDATA/42530005`.

Advanced pipeline options, such as asset pruning, plugin sorting with mlox and TES3Merge, are
described in [docs/pipeline.md](docs/pipeline.md).
