# Getting started

TES3X builds a modded copy of Xbox Morrowind on your PC, then copies it to a modded Xbox or runs
it in xemu. This page gets TES3X onto your PC and takes you through a first build.

The examples use Windows and PowerShell. On Linux, TES3X runs from pipx or a checkout: write
paths with `/`, and the data folder is `~/.local/share/tes3x`.

## What you need

- Your own copy of *Morrowind Game of the Year Edition* for the Xbox, unmodified: a folder
  holding `Default.xbe`, `morrowind.xbe`, `Morrowind.ini` and `Data Files`. TES3X never changes
  it; every build is a new folder.
- Somewhere to play:
  - a softmodded or hardmodded Xbox running an FTP server, as most modded dashboards do; or
  - [xemu](xemu.md), to try builds on the PC. It needs an MCPX boot ROM and a BIOS dumped from
    your own console, and [extract-xiso](https://github.com/XboxDev/extract-xiso). The GUI can
    download xemu and a blank hard disk image.
- Optionally, mods. TES3X installs them into a [mod library](mod-library.md), a folder with one
  folder per mod.
- An internet connection the first time a profile sorts its plugins with mlox, which downloads
  its rules once.

## Install TES3X

Pick one:

| Setup | For | You run |
|---|---|---|
| [Portable folder](#portable-folder) | Windows; nothing else to install | `TES3X.exe`, `tes3x-cli.exe` |
| [pipx](#pipx) | Windows or Linux, with Python already installed | `tes3x gui`, `tes3x` |
| [Checkout](development.md#setting-up-a-checkout) | working on TES3X itself | `tes3x`, in its virtual environment |

The rest of the docs write commands as `tes3x COMMAND`. In a portable folder, type
`.\tes3x-cli.exe COMMAND` in a terminal opened in that folder instead.

### Portable folder

Download `TES3X-<version>.zip` from the
[releases page](https://github.com/stjiub/tes3x/releases) and unpack it into a short path, such
as `C:\TES3X`. It carries Python, 7-Zip and LLVM, so nothing else needs installing.

- `TES3X.exe` starts the GUI.
- `tes3x-cli.exe` runs commands: `.\tes3x-cli.exe --help` lists them.

To update, unpack the new release beside the old one and delete the old folder. Your settings,
profiles and builds are kept in the [data folder](#where-tes3x-keeps-your-files), not in the
portable folder.

### pipx

You need Python 3.12 or newer and [pipx](https://pipx.pypa.io), and:

- [LLVM](https://releases.llvm.org) (`clang` and `lld-link`), for engine fixes and the default
  `delta-bsa` packaging. Without it, build with no engine fixes and `merged-bsa` or `loose`
  packaging. Put it on `PATH`, or set **LLVM tools** in the GUI's settings.
- [7-Zip](https://www.7-zip.org), only to install `.7z` and `.rar` mods.

```powershell
pipx install "tes3x[gui] @ git+https://github.com/stjiub/tes3x"
```

`tes3x gui` starts the GUI; `tes3x --help` lists the commands. Leave out `[gui]` for the command
line alone. `pipx reinstall tes3x` updates it.

## Where TES3X keeps your files

TES3X keeps your settings, profiles and builds in its data folder, `%LOCALAPPDATA%\TES3X` on
Windows (`~/.local/share/tes3x` on Linux, or `TES3X_DATA` when set):

| In the data folder | What it is |
|---|---|
| `tes3x.local.toml` | the **local config**: where your game and mods are, and your Xbox and xemu targets |
| `profiles\` | one **profile** per build: its mods, plugin order, patches and settings |
| `build\` | one folder per built profile; the game folder itself is its `deploy\` folder |

The GUI creates them there. On the command line, `tes3x init` copies the bundled example config
and profile into the data folder, keeping any files that already exist. `tes3x init FOLDER`
creates them elsewhere. Config discovery works from any directory; the examples below use
the data folder so profile paths stay short:

```powershell
tes3x init
cd $env:LOCALAPPDATA\TES3X
```

`--config FILE` uses another local config for one command, and `paths.profiles` and
`paths.build_root` in it move the other two; see the [configuration reference](configuration.md).

Words the docs use:

- **Clean game root**: your unmodified game folder (`paths.vanilla_root`).
- **Mod library**: the folder your mods are installed into (`paths.mod_library`).
- **Target**: a machine that runs builds, an Xbox or an xemu setup, named in the local config.
- **Games root**: the folder on an Xbox that holds game folders, such as `F:/Games`.
- **Install folder**: the folder a profile is deployed to, below the games root, such as
  `MorrowindModded`.

## Your first build

Each step is shown in the GUI first, then on the command line.

1. **Set your paths.** On first start the GUI opens **File > Settings**: set the **Clean game
   root** and the **Mod library**.

   On the command line, run `tes3x init`, then edit `tes3x.local.toml` in the data folder
   and set `paths.vanilla_root` and `paths.mod_library`.

2. **Add a target.** In the **Target** workspace, add an Xbox or xemu target with the cog beside
   the target list, and fill it in on the **Setup** tab: for an Xbox, its IP address and games
   root. **Test FTP** tries the login.

   On the command line, set the example's `[targets.xbox]` `host` and `games_root`. For xemu, see
   [xemu](xemu.md).

3. **Make a profile.** Choose **New** from the cog beside the profile list and name the build.
   On the **Build** tab, set its install folder. Tick mods on **Mods**, then look over
   **Plugins** and **Patches**. **Install mod…** adds an archive to the library.

   On the command line, edit the `profiles/my-build.toml` created by `tes3x init`.
   Set `profile.name` and `profile.install_dir`, and list your mods as `[[mods]]`
   blocks; for engine fixes only, remove every `[[mods]]` block. The example's comments explain
   each option.

4. **Check it.** **Actions > Check profile** resolves the profile without building: mods, missing
   masters, patches and paths.

   ```powershell
   tes3x pipeline profiles\my-build.toml --check
   ```

5. **Build it.** **Actions > Build profile**. The game folder is written to
   `build\my-build\deploy`.

   ```powershell
   tes3x pipeline profiles\my-build.toml
   ```

6. **Try it in xemu** (optional). With an xemu target selected, **Play** runs the build.

   ```powershell
   tes3x test profiles\my-build.toml
   ```

   boots it, starts a new game and walks to Balmora; see [xemu](xemu.md) for its setup.

7. **Deploy it.** A deploy makes the install folder on the Xbox match the build: files there that
   are not in the build are deleted. Give every build its own install folder, never the folder of
   an install you want to keep. Saves and the dashboard's `_resources` folder are not touched,
   and TES3X asks before replacing a folder that holds something other than this profile.

   **Actions > Deploy to Xbox…** uploads the build. On the command line, `--dry-run` first lists
   what is in the Xbox folder and what would change, without changing anything:

   ```powershell
   tes3x pipeline profiles\my-build.toml --dry-run
   tes3x pipeline profiles\my-build.toml --deploy
   ```

   The FTP login defaults to `xbox`/`xbox`. Set `TES3X_FTP_PASSWORD` or pass `--ask-password` to
   keep a password out of the local config.

Then start the install folder's `default.xbe` from the Xbox's dashboard.

## Next

- [GUI](gui.md): every tab and workspace.
- [Mod library](mod-library.md): installing mods, versions and optional parts.
- [Pipeline](pipeline.md): what each build step does, packaging and plugin ordering.
- [Patches](patches.md): the engine fixes, and the presets that select them.
- [Deployment](deployment.md): the console manager, shared retail bases and getting files back.
