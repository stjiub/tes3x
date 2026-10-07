# GUI

The GUI edits everything a profile and the local config hold, installs mods into the library, and
checks, builds, tests and deploys. In a [portable folder](#portable-folder), double-click
`TES3X.exe`. From a checkout, install its packages and start it with Python:

```powershell
python -m pip install -r requirements-gui.txt
python tools/tes3x_gui.py
```

### Portable folder

`python tools/tes3x_package.py --zip` writes `build/package/TES3X-<version>/` and its zip: the
tools, an embedded Python 3.12 with the GUI's packages, and `TES3X.exe`, which starts the GUI with
that Python. `externals/` holds 7-Zip, for `.7z` and `.rar` mods, and clang and lld-link from
LLVM, for engine fixes, each with its license. Nothing needs installing on the PC that runs it;
the **LLVM tools** setting still overrides the bundled LLVM.

It also carries the [console manager](deployment.md#installing-the-console-manager) as
`manager/default.xbe` and its launcher as `manager/launcher.xbe`, built with nxdk
(`paths.nxdk`), or taken from `--manager XBE` and `--launcher XBE`.

Building the folder needs a C compiler for `TES3X.exe` (MSYS2's `gcc` or `clang`, or `--cc`). The
first build downloads Python, 7-Zip and LLVM (about 880 MB, mostly the LLVM release), checks each
against a pinned SHA-256, and keeps them in `%LOCALAPPDATA%\TES3X\package` for later builds.

The version is `X.Y.Z` at a `vX.Y.Z` tag, and `X.Y.Z-dev.N+gSHA` for a commit N past it, with
`.dirty` when the checkout has uncommitted changes. The GUI shows it in its title.

The GUI opens the last profile you used and lists everything in `profiles/` for switching, with
New, Duplicate, Rename and Delete under the cog beside the list. On first start, with no `tes3x.local.toml` yet, it opens the local settings.

Three workspaces sit at the left of the toolbar: **Profile** edits the open profile (the tabs
below), [**Targets**](#targets) shows what the selected Xbox or xemu target is doing, and
[**Server**](#server) runs the multiplayer server. The toolbar and the command output stay the
same in all three.

## Profile

- **Mods** lists every mod in the library. Tick the ones this profile uses and drag them into
  order; lower mods win file conflicts. **Conflicts** counts the files a mod overrides (+) and
  loses (-), and selecting a mod highlights the mods it beats and the ones that beat it.
  **Xbox** shows the verdict from the [compatibility catalog](catalog.md): ✓ works, * works with
  requirements, ✗ doesn't work, ? not known. Hover over it for the requirements; it turns red
  when a patch the mod needs is off.
  Right-click a mod to send it to the top or bottom, pick its version or optional parts, ship it
  loose, open, rename, reinstall or delete it. With no `library.toml`, mods are written to the
  profile by folder name.
  Selecting a mod shows what it is in the details pane on the right: its author and summary from
  [Nexus Mods](https://www.nexusmods.com/morrowind), its page, the catalog's verdict, and each
  plugin's own description. The contents below it split the mod into plugins, archives and other
  files and show whether each reaches the build, is disabled or is overridden. The page comes from
  `library.toml`, the catalog or a Nexus download's file name; for any other mod, **Find on
  Nexus…** searches by name. What Nexus returns is saved in `library.toml` as the mod's `url`,
  `author` and `summary`.
- **Install mod…**, or dropping files on the list, adds a `.zip`, `.7z` or `.rar` archive (the
  last two need [7-Zip](https://www.7-zip.org)), a folder or a single plugin to the library. It
  shows the files and ticks the `Data Files` folder it found; for mods with numbered option
  folders it ticks the core one, and later folders overwrite earlier ones. Right-click a folder to
  mark it as `Data Files` when the layout isn't recognised. A Nexus file name fills in the name
  and version.
- **Plugins** lists the plugins of the ticked mods. Untick one to leave it out; drag them to set
  the load order, or press **Sort** to order them with mlox. Plugins whose masters are missing
  or load later are shown in red. **Archives** lists the archives the build ships and whether
  the game opens them. A mod's own `.bsa` archives are unpacked into the build unless you
  right-click the mod and choose **Load archives with multi-bsa**. **Data files** shows which mod
  each file comes from and what it overrides. Selecting a plugin, archive or file explains it in
  the details pane; a file shows its complete provider chain and whether it will be loose or packed.
  Patches and INI settings use the same pane.
- **Patches** is a checklist grouped by category, starting from a preset. Changes are saved as
  the profile's `enable` and `disable` lists. Every patch is listed, including informational rows
  for command-line and build options. Development-channel patches remain read-only until
  **View > Developer mode** is on.
- **INI** shows the `Morrowind.ini` the build ships: the retail values, and the `[Xbox]` keys of
  the patches that are on. Double-click a value to change it; only changed values go in the
  profile. When a patch is turned off, the values only it reads are dropped.
- **Resources** shows each active mod's source file and texture footprint before conversion and
  packing. Its dependency view combines mod dependencies and plugin masters and marks anything
  missing or ordered too late.
- **Build** holds everything else in the profile: dashboard title, install folder, full or shared-base
  install layout, mod library, packaging mode, texture and file-name rules and player preferences.
  **Skip the logo and New Game movies** sets the two `[Movies]` keys in the INI tab to a missing
  file, which the game skips. Its details pane previews the current mod and plugin order, asset
  packaging, archives, engine patches and deployment destination before the profile is built.

Settings left at their defaults stay out of the profile file. **File > Settings** holds the paths
shared by every profile and the add-ons; targets are set up in the [Targets](#targets) workspace.

The toolbar target chooses where checks, builds, deploys, saves, logs and Play go. Its dot is grey
before an Xbox is checked or for xemu, green while an Xbox answers, and red when it is unreachable;
the tooltip carries its address and reported free space. The joined Check, Build and Deploy buttons
use grey, green, amber and red dots for not run, current, stale and failed. Deploy is unavailable
for xemu targets. An Xbox whose [console manager](deployment.md#through-the-console-manager) is
paired with this PC shows **Manager**, and Deploy then goes through the manager instead of FTP.
The cog beside the target list adds, duplicates or removes a target, or opens its Setup.
**Actions** also smoke-tests the current profile. The status bar is reserved for
activity, transient messages and counts. Check and per-target Deploy results are
remembered in the build output; like the Build state, they notice profile edits but not changes
inside mod folders.

For a shared-base layout, local settings also name the clean retail folder on the Xbox. Deployment
shows that path before explicitly installing or synchronizing it, then sends the smaller profile
folder.

**Play** runs the profile on the selected target, building it first when necessary. A 128 MB xemu
target needs a BIOS that uses the extra memory, set in the target's **Setup**. Each profile keeps its
own xemu hard disk, so saves carry over between sessions; the Play menu can reset it, enable GDB,
pull Xbox logs or refresh the selected Xbox connection. With the [console add-on](addons.md)
switched on, Play on an Xbox target deploys the build and starts the game there. While xemu runs,
Play becomes **Stop** (`Shift+F9`). Check and Deploy remain available; Build waits until the
running emulator releases that build.

**Saves** chooses the profile's save pool: the retail game's shared saves, or a pool of its own
that keeps its saves apart from other builds (see `save_pool` in
[configuration.md](configuration.md)). It lists that pool everywhere at once, grouped by device:
the PC save library (`build/saves/<pool>/`), the profile's persistent xemu disk, and every
configured Xbox. Each Xbox keeps its own last listing, so the tab opens without waiting for the
consoles and still shows one while it is off; each is asked again once per session, or on
**Refresh**. Saves that need plugins the profile does not load are marked. **Copy to…** sends the
selected saves to the device you choose, through the PC library; right-click for the same choice,
for copying or moving saves to another pool where they are, or for deleting them. A move deletes
the original only after the copy is complete; changes to the xemu disk are stacked on it as a new
layer.

## Targets

**Targets** follows the toolbar target. Its actions are offered by what the target can do now:
FTP, the dashboard agent, the in-game [agent](../patches/agent.md) or a running xemu. An
unavailable action stays visible and its tooltip says what is missing.

- **Overview** has one section for each thing that can answer, with that section's actions.
  **Connection** shows the address, memory, FTP and drive space, with Check connection and Pull logs.
  **Agent** shows whether a game or the console manager is connected and, for a game, its
  heartbeat (frame time, free memory, dropped log lines), with Quit to dashboard and Fetch
  file. On an Xbox, **Dashboard agent** has Install / update, Restart dashboard and Remove, and
  **Console manager** shows the installed version beside the one this PC would install, with
  Install / update.
- **Console** streams the running game's log and runs console lines in it, such as
  `player->getpos x` or `tes3xnet stat`; Up and Down recall earlier lines. It needs a build with
  the `agent` and `console` patches. **Fetch file…** copies a file such as `E:\tes3xprof.bin` from
  the running game into `build/agent-fetch/<target>/<time>/`.
- **Logs** lists every pulled, fetched and xemu-recovered log for this target, this target and
  profile, or everything, and shows one raw or as a crash and hang summary. **Pull logs** copies
  `E:\tes3x*` over FTP into `build/xbox-logs/<target>/<time>/`, beside a `pull.json` naming the
  target and profile; while a game is running, and FTP with it is gone, it fetches the log through
  the in-game agent instead.
- **Builds** lists the game folders on an Xbox and, for those TES3X deployed, the profile, save
  pool and time of the deploy; the open profile's folder is bold.
- **Setup** edits the target. An Xbox has its own FTP login, games root, optional shared retail
  base and dashboard root; common XBMC4Gamers layouts are detected when the root is blank, and
  **Test FTP** tries the FTP login before saving, and **Test agent** asks the saved target's
  dashboard agent to answer. An xemu target has its emulator,
  firmware, clean disk and memory size, and can download xemu. **Save** writes the target to
  `tes3x.local.toml`, keeping its comments. A legacy `[deploy]` configuration is left alone until
  **Convert** is pressed.

## Server

**Server** manages either a server on this PC or a remote one. **A server on this PC** starts
`tes3x_net.py serve` with the settings in the form, which are kept in
the `[server]` table of `tes3x.local.toml` (see [configuration](configuration.md#server)). It shows
the server's output and polls the admin port for the connected consoles; Kick, Ban and Ask all to
save act on them. **Stop** asks every console to save its character before the server exits;
pressing it again stops at once. Closing the GUI stops a server it started. **A remote server**
connects to a server's remote admin port (see
[remote admin](multiplayer.md#remote-admin)) with its address and admin password, then lists
the consoles and offers the same Kick, Ban, Ask all to save and Bans, and Stop server. The
password is kept in the local config only if you tick **Remember**. See
[multiplayer](multiplayer.md) for what the settings mean.
