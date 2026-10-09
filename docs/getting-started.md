# Getting started

## Before you start

You need:

- A Windows PC. In a [portable folder](gui.md#portable-folder), which carries Python, its
  packages, 7-Zip and LLVM, nothing else needs installing; skip the next three items. From a
  checkout:
  - Python 3.12 or newer, with TES3X installed into a virtual environment from the checkout
    (see [installing from a checkout](#installing-from-a-checkout)). The command line needs only
    Pillow, `zstandard` (patched XBEs are stored as deltas) and, for
    `multiplayer` and `agent` builds, `cryptography`; the GUI also needs PySide6 and tomlkit.
  - [LLVM](https://releases.llvm.org) (`clang` and `lld-link`), for engine fixes and the default
    `delta-bsa` packaging. Without it, build with no engine fixes and `merged-bsa` or `loose`
    packaging.
  - [7-Zip](https://www.7-zip.org), only to install `.7z` and `.rar` mods.
- Your own copy of *Morrowind Game of the Year Edition* for the Xbox, unmodified: a folder
  containing `Default.xbe`, `morrowind.xbe`, `Morrowind.ini` and `Data Files`. TES3X never changes
  it; every build is a new folder.
- A softmodded or hardmodded Xbox with an FTP server, or [xemu](xemu.md) to try builds on the PC.
  xemu also needs an MCPX boot ROM and a BIOS you dump from your own console, and
  [extract-xiso](https://github.com/XboxDev/extract-xiso); the GUI can download xemu and a blank
  hard disk image.
- An internet connection for the first build that sorts plugins with mlox, which downloads its
  rules once; set `paths.mlox_rules` to work offline.
- Optionally, mods: a [mod library](mod-library.md) is a folder with one folder per mod, each laid
  out the way the mod would sit in `Data Files`.

## Installing from a checkout

Skip this in a portable folder. Otherwise, once:

```powershell
git clone https://github.com/stjiub/tes3x
cd tes3x
python -m venv .venv
.venv\Scripts\activate
python -m pip install -e .[gui]
```

Activate the environment (`.venv\Scripts\activate`) in each new terminal; the `tes3x` command
is on `PATH` while it is active. Without `[gui]` it installs the command line alone.

## With the GUI

1. Start it: in a portable folder, double-click `TES3X.exe`; from a checkout, with its
   environment active:

   ```powershell
   tes3x gui
   ```

2. On first start the GUI opens **File > Settings**. Set the clean game root and your mod library,
   and **LLVM tools** if `clang` is not on `PATH`. Then add an Xbox target with the cog beside the
   target list, and give it its IP address and games root under **Targets > Setup**.
3. Choose **New** from the cog beside the profile list and name the build. On the **Build** tab, set its install
   folder, such as `MorrowindModded`.
4. Tick mods on **Mods**, and check **Plugins** and **Patches**. **Install mod…** adds an archive to
   the library.
5. **Actions > Check profile** resolves the profile without building.
6. **Actions > Build profile** builds it.
7. Optionally, **Play** runs it in xemu, once an xemu target is set up under **Targets > Setup**.
8. **Actions > Deploy to Xbox…** uploads it. It asks before replacing a folder that holds
   something other than this profile.

The [GUI guide](gui.md) describes every tab.

## From the command line

These steps run in a checkout with its environment active (see
[installing from a checkout](#installing-from-a-checkout)). In a portable folder, run
`tes3x-cli.exe` from it in place of `tes3x`.

1. Copy the example local config and an example profile:

   ```powershell
   cp examples/local.toml tes3x.local.toml
   mkdir profiles
   cp examples/profile.toml profiles/my-build.toml
   ```

2. In `tes3x.local.toml`, set `paths.vanilla_root` to the retail game folder, then set the Xbox
   target's `host` and `games_root`. Set `paths.llvm` if `clang` is not on `PATH`.
3. In `profiles/my-build.toml`, set `profile.name`, `profile.library` and `profile.install_dir`,
   the folder created below the target's games root, and list your mods as `[[mods]]` blocks.
   For engine fixes only, remove `library` and every `[[mods]]` block. The example's comments
   explain each option; the [configuration reference](configuration.md) lists them all.
4. Check the profile:

   ```powershell
   tes3x pipeline profiles/my-build.toml --check
   ```

5. Build it. The game folder is written to `<build_root>/my-build/deploy`:

   ```powershell
   tes3x pipeline profiles/my-build.toml
   ```

6. Optionally, boot it in xemu and walk to Balmora (see [xemu](xemu.md) for the setup):

   ```powershell
   tes3x test profiles/my-build.toml
   ```

7. Build and preview the upload. This lists what is already in the Xbox folder and what would
   change:

   ```powershell
   tes3x pipeline profiles/my-build.toml --dry-run
   ```

8. Deploy:

   ```powershell
   tes3x pipeline profiles/my-build.toml --deploy
   ```

## Deploy to a folder of its own

A deploy makes the Xbox folder match the build: files there that are not in the build are
deleted. Give every build its own folder, never the folder of an install you want to keep. Saves
and the dashboard's `_resources` folder are not touched, and deploy refuses a folder that holds
another profile until you confirm with `--replace-remote`.

The FTP login defaults to `xbox`/`xbox`. Set `TES3X_FTP_PASSWORD` or pass `--ask-password` to keep
a password out of the config file. Any other FTP client can also copy the build folder.

## Next

- [Pipeline](pipeline.md): what each build step does, packaging modes and plugin ordering.
- [Patches](patches.md): the engine fixes, and the presets that select them.
- [Testing](testing.md): from unit tests to game tests on a console.
- [Add-ons](addons.md): launching builds on the Xbox from the PC.
