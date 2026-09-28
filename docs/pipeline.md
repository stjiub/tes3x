# Pipeline options

`tes3x_pipeline.py` runs the whole build: collect mods, pack, patch the XBE and deploy. Each of
those steps is also a tool you can run by itself. Every profile and local-config key is in the
[configuration reference](configuration.md).

## Building

```powershell
python tools/tes3x_pipeline.py profiles/my-build.toml --check
python tools/tes3x_pipeline.py profiles/my-build.toml --dry-run
python tools/tes3x_pipeline.py profiles/my-build.toml --deploy
```

Output goes to `BUILD_ROOT/PROFILE_NAME`. The pipeline only overwrites an empty folder or one it
built before. If a step fails, what it produced so far is left in place so you can look at it.

A profile with no mods skips collecting and packing and ships the retail `Data Files` unchanged.
`[ini]` settings still apply. `--ini-set` on the command line overrides the same key in `[ini]`.

The presets:

- `minimal`: no optional patches.
- `standard`: tested `core` and `correctness` fixes, plus the in-game console.
- `development`: everything in `standard`, untested `core` and `correctness` fixes, and
  diagnostics.

Anything else has to be enabled by name or by category. Some patches are added automatically
when something needs them; `delta-bsa` packing adds `multi-bsa`, for example.

### Expansion master placeholders

Xbox GOTY keeps the expansion content in `Morrowind.esm`, while plugins still name
`Tribunal.esm` and `Bloodmoon.esm` as masters. For a modded build, TES3X copies either file when
an input supplies it and otherwise generates a four-byte file containing only `TES3`. These
placeholders satisfy the dependency names; they do not add or replace expansion content.

This is an automatic packaging compatibility step, not an XBE patch. A profile with no mods
stages the retail `Data Files` unchanged and does not generate missing placeholders.

### Loose files

`mode = "loose"` ships every mod file loose and leaves retail `Morrowind.bsa` as it is. In an
archive build, `loose = true` on a mod, or a pattern in `loose_assets`, keeps just those files
loose.

When a loose file replaces a retail asset, the retail copy is dropped from a merged archive, or
listed in `ArchiveInvalidationList.txt` when the retail archive is left alone. Loose files need
`TryArchiveFirst=0`, which the build sets, so they can't be combined with `archive_only`.

A loose build avoids archive limits, such as two file names that hash the same, but the Xbox
searches each folder entry by entry, and a large mod list puts over a thousand files in some
folders.

### Sorting plugins with mlox

By default, plugins load masters first, then in mod order. A profile can list its own order in
`[plugins] order`; the GUI writes it when you drag plugins or press **Sort**, which runs mlox once.
With `plugin_order = "mlox"` in the profile's `[rules]`, [mlox](https://github.com/mlox/mlox) sorts them using the community's
ordering rules instead. File conflicts between mods still go by mod order; mlox only changes the
plugin load order.

mlox isn't included with TES3X. To set it up:

1. `python -m pip install mlox`. Add `--no-deps` to skip its GUI's dependencies, which TES3X
   doesn't use.
2. Get the rules: **Download** next to "mlox rules" in the GUI's local settings, or
   `python tools/tes3x_plugins.py fetch-rules mlox/mlox_base.txt`, then set `paths.mlox_rules` to
   that file. They come from the
   [mlox-rules project](https://github.com/DanaePlays/mlox-rules) and change often, so download
   them again now and then.

mlox runs on a copy of the build's plugins and never touches your library. Its conflict and
missing-requirement warnings are printed during the build. Everything it said, including notes,
goes to `mlox-messages.txt` in the build folder, and the order it picked to `mlox-order.json`.
Many notes are advice for the PC version and don't apply to the Xbox.

### Discarding the build after deploying

```powershell
python tools/tes3x_pipeline.py profiles/my-build.toml --deploy --verify-deploy size --discard-build
python tools/tes3x_pipeline.py profiles/my-build.toml --deploy --verify-deploy hash --discard-build
```

`size` re-lists the uploaded files. `hash` downloads every file again and compares it, which
roughly doubles the transfer. The build is only deleted once the check passes.

## Xbox paths

`remote_root` is the game folder on the Xbox, such as `F:/Games/MorrowindTest`, not its
`Data Files` folder. The default is `F:/Games/Morrowind`. Set it in the profile, the local config
or with `--remote-root`.

FATX limits each file or folder name to 42 characters and a full path to 250 (not counting the
drive letter). The build checks both before anything is uploaded. Names are never shortened
automatically, since plugins and meshes refer to files by name. A shorter `remote_root` helps
with long paths but not with a single name that is too long. Names inside a BSA don't count.

Deploy keeps `tes3xdeploy.json` in the game folder with the size and SHA-1 of every file it sent,
and only resends files that changed. When any plugin changes, all plugins are resent so their
load order is stamped again.

## Pruning unused assets

```powershell
python tools/tes3x_build.py profiles/my-build.toml --prune `
  --vanilla "build/vanilla/Data Files" `
  --out build/pruned-tree --reachability-json build/reachability.json
```

`--prune` drops mod assets that nothing refers to. It follows references from records in the
masters and plugins, from meshes to their textures and animations, from books to their images and
from scripts to literal paths. When unsure it keeps the file. It only saves disk space, not RAM,
since an asset nothing loads never used RAM anyway.

Assets picked at runtime by name, rather than referenced directly, won't be found. Keep them with
`[rules].keep_assets = ["textures/custom_dynamic/*"]`. The JSON report lists why each file was
kept and what was removed.

## Invalidating archived assets

`tes3x_pack.py --loose-asset "textures/example.dds"` puts a file both in the archive and loose,
and lists it in `ArchiveInvalidationList.txt` so the loose copy wins. It sets `TryArchiveFirst=0`
and can't be used with `--archive-only`.

## Sound and mesh checks

`tes3x_build.py --sox PATH --sound-rate 22050` resamples mod WAVs to at most that rate. It never
raises the sample rate and keeps the channel count. Compressed WAVs are left alone.

`python tools/tes3x_assets.py build/pruned-tree --json build/assets.json` flags malformed NIF
headers and missing texture references, and lists WAV formats. It doesn't check geometry,
skinning or anything else that can crash the renderer.

`python tools/tes3x_map.py "Data Files/Morrowind.esm.map"` dumps a map companion file and can
save the first map image with `--preview tile.png`.
