# GUI

The GUI edits everything a profile and the local config hold, installs mods into the library, and
checks, builds, tests and deploys. It needs PySide6 and tomlkit:

```powershell
python -m pip install -r requirements-gui.txt
python tools/tes3x_gui.py
```

The GUI opens the last profile you used and lists everything in `profiles/` for switching, with
New, Duplicate, Rename and Delete beside the list. On first start, with no `tes3x.local.toml` yet, it opens the local settings.

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

Settings left at their defaults stay out of the profile file. **File > Settings** uses categories
for paths, targets, the shared xemu installation and add-ons. Targets can be
added, duplicated or removed; an Xbox target has its own FTP login, games root and optional shared
retail base, while an xemu target chooses 64 or 128 MB. The connection button tests an Xbox's FTP
login before saving. A legacy `[deploy]` configuration is left alone until **Convert** is pressed.

The toolbar target chooses where checks, builds, deploys, saves, logs and Play go. Its dot is grey
before an Xbox is checked or for xemu, green while an Xbox answers, and red when it is unreachable;
the tooltip carries its address and reported free space. The joined Check, Build and Deploy buttons
use grey, green, amber and red dots for not run, current, stale and failed. Deploy is unavailable
for xemu targets. **Actions** also smoke-tests the current profile. The status bar is reserved for
activity, transient messages, counts and the profile path.

For a shared-base layout, local settings also name the clean retail folder on the Xbox. Deployment
shows that path before explicitly installing or synchronizing it, then sends the smaller profile
folder.

**Play** runs the profile on the selected target, building it first when necessary. A 128 MB xemu
target needs a BIOS that uses the extra memory, set in **File > Settings**. Each profile keeps its
own xemu hard disk, so saves carry over between sessions; the Play menu can reset it, enable GDB,
pull Xbox logs or refresh the selected Xbox connection. With the [console add-on](addons.md)
switched on, Play on an Xbox target deploys the build and starts the game there.

**Saves** chooses the profile's save pool: the retail game's shared saves, or a pool of its own
that keeps its saves apart from other builds (see `save_pool` in
[configuration.md](configuration.md)). It lists the pool's saves on the Xbox, on the profile's xemu
disk and in the PC save library (`build/saves/<pool>/`), and marks those that need plugins the
profile does not load. The last Xbox listing is kept, so the tab opens without waiting for the
console and still shows it while the console is off. Right-click saves to pull them to the PC,
push them to the Xbox or xemu, copy or move them to another pool, or delete them. A move deletes
the original only after the copy is complete; changes to the xemu disk are stacked on it as a new
layer.
