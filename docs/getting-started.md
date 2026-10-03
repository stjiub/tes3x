# Getting started

This page takes you from a clean copy of the game to a modded build running on your Xbox. There are
two routes to the same result: the [GUI](#with-the-gui), or [the command line](#from-the-command-line).
Both follow the same steps:

1. Supply a clean retail game.
2. Tell TES3X where things are.
3. Create a profile.
4. Check it.
5. Build it.
6. Optionally, run it in xemu.
7. Preview the deploy.
8. Deploy it to its own folder on the Xbox.

## Before you start

You need:

- Python 3.12 or newer and Pillow: `python -m pip install Pillow`.
- Your own copy of *Morrowind Game of the Year Edition* for the Xbox, unmodified: a folder
  containing `Default.xbe`, `morrowind.xbe`, `Morrowind.ini` and `Data Files`. TES3X never changes
  it; every build is a new folder.
- [LLVM](https://releases.llvm.org) (`clang` and `lld-link`), for engine fixes and the default
  `delta-bsa` packaging. Without it, build with no engine fixes and `merged-bsa` or `loose`
  packaging.
- A softmodded or hardmodded Xbox with an FTP server, or [xemu](xemu.md) to try builds on the PC.
- Optionally, mods: a [mod library](mod-library.md) is a folder with one folder per mod, each laid
  out the way the mod would sit in `Data Files`.

## With the GUI

1. Install the GUI's packages and start it:

   ```powershell
   python -m pip install -r requirements-gui.txt
   python tools/tes3x_gui.py
   ```

2. On first start the GUI opens **File > Settings**. Set the clean game root and your mod library,
   then add an Xbox target with its IP address and games root. Set **LLVM tools** if `clang` is not
   on `PATH`.
3. Press **New** beside the profile list and name the build. On the **Build** tab, set its install
   folder, such as `MorrowindModded`.
4. Tick mods on **Mods**, and check **Plugins** and **Patches**. **Install mod…** adds an archive to
   the library.
5. **Actions > Check profile** resolves the profile without building.
6. **Actions > Build profile** builds it.
7. Optionally, **Play** runs it in xemu, once xemu is set up in **File > Settings**.
8. **Actions > Deploy to Xbox…** uploads it. It asks before replacing a folder that holds
   something other than this profile.

The [GUI guide](gui.md) describes every tab.

## From the command line

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
   python tools/tes3x_pipeline.py profiles/my-build.toml --check
   ```

5. Build it. The game folder is written to `<build_root>/my-build/deploy`:

   ```powershell
   python tools/tes3x_pipeline.py profiles/my-build.toml
   ```

6. Optionally, boot it in xemu and walk to Balmora (see [xemu](xemu.md) for the setup):

   ```powershell
   python tools/tes3x_test.py profiles/my-build.toml
   ```

7. Build and preview the upload. This lists what is already in the Xbox folder and what would
   change:

   ```powershell
   python tools/tes3x_pipeline.py profiles/my-build.toml --dry-run
   ```

8. Deploy:

   ```powershell
   python tools/tes3x_pipeline.py profiles/my-build.toml --deploy
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
