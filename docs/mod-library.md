# Mod library

The mod library is a folder of mods, one folder each, laid out the way each mod would sit in
`Data Files`. TES3X never changes the mods in it. A build stacks the mods a profile picks in
order, later ones winning when two ship the same file, and packs the result for the Xbox.

A profile can pick a mod in two ways, and the command line and the GUI handle both:

- **By folder:** `name = "Folder Mod"` takes that whole folder. Nothing else is needed.
- **By id:** `id = "folder-mod"` looks the mod up in a `library.toml` at the top of the library,
  which can list several versions of a mod and its optional installer folders.

A profile can mix the two. See [`examples/library.toml`](../examples/library.toml).

## Indexing a library

```powershell
python tools/tes3x_library.py scan "D:/Morrowind Mods"          # list folders not indexed yet
python tools/tes3x_library.py scan "D:/Morrowind Mods" --write  # add them to library.toml
```

Each new folder or loose plugin becomes one mod with version `unknown`. Entries already in
`library.toml` are left exactly as they are, so run it again whenever you add mods. Versions and
optional folders are filled in by hand afterwards. In the GUI this is **File > Index
new library folders**.

To switch a profile from folder names to ids:

```powershell
python tools/tes3x_library.py convert profiles/my-build.toml
```

It converts each mod whose folder is indexed as a whole, with no optional components, so the
build stays the same. Others keep their folder name, with the reason printed. `--dry-run` only
reports. In the GUI this is **File > Convert folder names to library ids**.

```toml
schema = 1

[[mod]]
id = "travel-mod"
name = "Travel Mod"

[[mod.release]]
version = "1.0"
folder = "Travel Mod 1.0"
default = true
roots = ["00 Core"]
dependencies = ["shared-assets"]

[[mod.release.component]]
id = "travel-music"
name = "Travel music"
roots = ["10 Travel Music"]
default = false
```

A profile picks it like this:

```toml
[[mods]]
id = "travel-mod"
version = "1.0"
components = ["travel-music"]
plugins = ["TravelMod.esp"]
order = 10
```

`roots` are folders inside the release folder, applied in order: the release's own roots first,
then the chosen components in profile order. Components in the same `group` are alternatives, so
only one can be chosen, and `conflicts` lists components that can't be combined with this one.
`dependencies` names other mods by id; the GUI turns them on for you when you turn a mod on.
`source` records the archive a release was installed from, so the GUI can reinstall it.

When a component's folder sits inside a release root, as installers with an `Optional` folder
beside the main files often do, it is left out of the release until the component is chosen.

Set the library in the local config:

```toml
[paths]
mod_library = "D:/Morrowind Mods"
```

A profile's own `library` overrides it.

## GUI

```powershell
python -m pip install -r requirements-gui.txt
python tools/tes3x_gui.py
```

The GUI opens the last profile you used and lists everything in `profiles/` for switching. On
first start, with no `tes3x.local.toml` yet, it opens the local settings.

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
  each file comes from and what it overrides.
- **Patches** is a checklist grouped by category, starting from a preset. Changes are saved as
  the profile's `enable` and `disable` lists.
- **INI** shows the `Morrowind.ini` the build ships: the retail values, and the `[Xbox]` keys of
  the patches that are on. Double-click a value to change it; only changed values go in the
  profile. When a patch is turned off, the values only it reads are dropped.
- **Build** holds everything else in the profile: dashboard title, Xbox folder, mod library,
  packaging mode, texture and file-name rules and player preferences.

Settings left at their defaults stay out of the profile file.

**Actions** checks, builds, smoke-tests and deploys the current profile, and pulls logs off the
Xbox into `build/xbox-logs/`. **File > Settings** edits `tes3x.local.toml`. The status bar
shows whether the Xbox is reachable over FTP.

**Play** runs the profile in [xemu](https://xemu.app), building it first when the dot beside it
says it is out of date or not built. Its arrow chooses 64 MB or 128 MB; 128 MB needs a BIOS that
uses the extra memory, set in **File > Settings**. Each profile keeps its own xemu hard disk, so
saves carry over between sessions; **Actions > Reset xemu saves…** starts it clean. With the
[console add-on](addons.md) switched on, the arrow also offers **Xbox**: deploy to the console and
start the game there.
