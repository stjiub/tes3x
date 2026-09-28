# Mod library

The mod library is a folder of mods, one folder each, laid out the way each mod would sit in
`Data Files`. TES3X never changes the mods in it. A build stacks the mods a profile picks in
order, later ones winning when two ship the same file, and packs the result for the Xbox.

A profile can pick a mod in two ways, and the command line and the profile manager handle both:

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
optional folders are filled in by hand afterwards. In the profile manager this is **File > Index
new library folders**.

To switch a profile from folder names to ids:

```powershell
python tools/tes3x_library.py convert profiles/my-build.toml
```

It converts each mod whose folder is indexed as a whole, with no optional components, so the
build stays the same. Others keep their folder name, with the reason printed. `--dry-run` only
reports. In the profile manager this is **File > Convert folder names to library ids**.

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
`dependencies` names other mods by id; the profile manager adds them for you when you add a mod.

When a component's folder sits inside a release root, as installers with an `Optional` folder
beside the main files often do, it is left out of the release until the component is chosen.

Set the library in the local config:

```toml
[paths]
mod_library = "D:/Morrowind Mods"
```

A profile's own `library` overrides it.

## Profile manager

```powershell
python -m pip install -r requirements-gui.txt
python tools/tes3x_gui.py
```

The profile manager opens the last profile you used and lists everything in `profiles/` for
switching. On first start, with no `tes3x.local.toml` yet, it opens the local settings.

- **Mods** picks mods, versions, optional components and plugins, and sets their order. Each mod
  can also be turned off, skipped when its folder is missing, or shipped loose. With no
  `library.toml`, mods are added by folder name.
- **Patches** sets the preset and turns individual patches or whole categories on or off, with a
  summary of what the profile ends up applying.
- **Build** holds everything else in the profile: dashboard title, Xbox folder, mod library,
  packaging mode, texture and file-name rules, plugin order, player preferences and
  `Morrowind.ini` settings.

Settings left at their defaults stay out of the profile file.

**Actions** checks, builds, smoke-tests and deploys the current profile, and pulls logs off the
Xbox into `build/xbox-logs/`. **File > Local settings** edits `tes3x.local.toml`. The status bar
shows whether the Xbox is reachable over FTP.
