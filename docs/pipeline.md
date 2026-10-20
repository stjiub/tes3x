# Pipeline

`tes3x pipeline` turns one mod library profile into a complete game folder, and optionally deploys
it. It runs a fixed series of stages, each implemented by a tool you can also run individually (see
[commands](commands.md)). They are described here in the order their results fit together; the
payload and XBE patching run before plugins are ordered and packed. Every profile and local-config
key is in the [configuration reference](configuration.md).

```powershell
tes3x pipeline profiles/my-build.toml --check
tes3x pipeline profiles/my-build.toml
tes3x pipeline profiles/my-build.toml --dry-run
tes3x pipeline profiles/my-build.toml --deploy
```

## 1. Resolve and check

Reads `tes3x.local.toml` and the profile, resolves the preset and patch selection, the mods and the
package mode, and prints what would be built. `--check` stops here, so it checks the profile and
the patch selection but not the paths and tools a build needs; those are checked when the build
starts.

A profile with no mods is a patches-only build: stages 2 and 3 are skipped and the retail
`Data Files` ship unchanged. `[ini]` settings still apply.

Common failures:

- `unknown selectable patches: ...`, or patches both enabled and disabled: reported by `--check`.
- `set paths.vanilla_root in local config or pass --vanilla`: the local config does not name the
  clean game folder.
- `could not download the mlox rules from ...` or `mlox rules not found: PATH`: see
  [sorting plugins with mlox](#sorting-plugins-with-mlox).
- `deployment requires an Xbox target with host and games_root`.

### Choosing engine fixes

The profile's `[patches] preset` selects a baseline:

| preset | contents |
|---|---|
| `minimal` | no optional engine fixes |
| `recommended` | release fixes selected for general use |
| `testing` | `recommended`, preview fixes, diagnostics and the console |

`enable` and `disable` add or remove individual patches, and `categories` adds every patch in a
category; the [example profile](../examples/profile.toml) lists them all. `--preset`, `--enable` and
`--disable` on the command line override the profile. Some patches are added when something needs
them: `delta-bsa` packing (the default; see [packaging](packaging.md)) adds `multi-bsa`,
`mwse-legacy` adds `script-ext`, and `multiplayer` adds `net` and `diagnostics`. Every build gets
[`boot-media`](../patches/boot-media.md) and [`drive-letters`](../patches/drive-letters.md). See the
[patch table](patches.md), the [`[Xbox]` ini keys](ini-keys.md) patches read, or
`tes3x patch --list`.

## 2. Collect the winning files

`tes3x build` stacks the profile's mods in order, later mods winning when two ship the same
file, and writes the result as one `Data Files` tree. On the way it:

- drops files matching `rules.exclude` (by default documentation, images and stray `.ini` files);
- converts mod textures larger than `rules.max_texture_size` to fit, and reports the total texture
  size against retail's, since video memory is a total budget;
- checks file names against FATX's limits, and reports conflicts, the busiest folders and missing
  masters.

`== MISSING MASTERS` means a plugin names a master no mod supplies; on the Xbox such a plugin stalls
the loader. `mods not found in LIBRARY` means a profile names a mod folder or id the library does
not have.

## 3. Order plugins and pack

Plugins load in the order set by their file times. By default, masters load first, then plugins
in mod order. A profile can list its own order in `[plugins] order`; the GUI writes it when you
drag plugins or press **Sort**. With `rules.plugin_order = "mlox"`, mlox sorts them at build time.
`tes3x plugins` does the ordering.

The Xbox never loads a plugin whose name has more than one dot, such as
`Ports Of Vvardenfell V1.6.ESP`: the file is skipped without an error, and a plugin that needs it
as a master makes the game restart before the main menu, over and over. Packing therefore ships
such a plugin with its extra dots turned into underscores (`Ports Of Vvardenfell V1_6.ESP`) and
points every plugin that names it as a master at the new name. The build prints each rename. Load
order is unaffected, since it comes from file times. Plugins that were never loaded under the old
name appear in no save.

`tes3x pack` then packages the tree with the retail files according to `package.mode`:
`delta-bsa` (the default), `merged-bsa` or `loose`. See [packaging](packaging.md).

### Merging animation KFs

With [`animation.merge_kf`](configuration.md#animation), the pipeline combines the listed KF
tracks with the winning `Meshes/xbase_anim.kf` before packing. If the build has no loose base KF,
it reads the retail one from `Morrowind.bsa`. The listed source KFs are consumed in order. The
same operation is available as `tes3x kf-merge BASE ADDITION... --out OUTPUT` for a manual build.
The optional `tes3x[kf]` dependency supplies the KF reader and writer. Quadratic position and
scale curves appended to linear tracks are sampled at 30 Hz. The merger accepts NetImmerse
4.0.0.2 animation sequences and stops on unsupported key layouts.

A [catalog](catalog.md) mod can name its own KFs to merge; enabling the mod adds them to the
profile's `animation.merge_kf` list.

If the profile sets [`ui.scale`](configuration.md#ui), the pipeline writes smaller bitmap font
metrics into the staged `.fnt` files and enables the UI scale hook for HUD art, common menu
panels and supported fixed menus. `ui.widescreen_480p` narrows the staged glyph widths and spacing for a stretched 480p
display. The game keeps the same font texture atlases.

### Sorting plugins with mlox

With `plugin_order = "mlox"` in the profile's `[rules]`, [mlox](https://github.com/mlox/mlox) sorts
plugins using the community's ordering rules. File conflicts between mods still go by mod order;
mlox only changes the plugin load order.

TES3X includes mlox's sorter, so there is nothing to install. The rules come from the
[mlox-rules project](https://github.com/DanaePlays/mlox-rules): the first sort downloads them to
TES3X's data folder (see [local config](configuration.md#local-config)), in `mlox`, and later
sorts reuse that copy. The rules change often; `tes3x plugins fetch-rules` fetches the current
ones. To use your own rules file instead, set `paths.mlox_rules`.

mlox runs on a copy of the build's plugins and never touches your library. Its conflict and
missing-requirement warnings are printed during the build. Everything it said, including notes,
goes to `mlox-messages.txt` in the build folder, and the order it picked to `mlox-order.json`.
Many notes are advice for the PC version and don't apply to the Xbox.

### Conflict patch with TES3Merge

With `tes3merge = true` in the profile's `[rules]` and `paths.tes3merge` pointing at
`TES3Merge.exe`, the build runs [TES3Merge](https://github.com/NullCascade/TES3Merge) over its
plugins in load order and ships the result, `Merged Objects.esp`, as the last plugin. TES3Merge
combines the changes several mods make to the same record, so that the last-loaded mod no longer
wins outright. It adds a plugin rather than replacing any. Its default fixes also apply: summoned
creatures are flagged persistent, and cells with zero fog density get a small nonzero value.

TES3Merge runs on a copy staged with the Xbox's own `Morrowind.esm`. Your library and any other
Morrowind install are left alone. Its log stays in the build's `tes3merge` folder. Releases
target .NET 6; the build lets a newer installed .NET runtime run it. When nothing conflicts,
no patch is shipped.

`tes3x plugins merge TREE --vanilla "Data Files" --tool TES3Merge.exe --work DIR
--out "Merged Objects.esp"` runs the same step alone; `--order` takes an order file from `order`
or `arrange`.

## 4. Build the payload and patch the XBE

When a selected patch needs code, the pipeline links a [payload](../patches/payload.md) from
the bundled objects of exactly the selected patches, for the retail XBE it is building from.
Any combination of patches builds without a compiler. Region selection is automatic; no
PAL/NTSC profile setting is needed. A payload source changed
since its bundled object was built is compiled again with clang, as is every source when
`--build-payload` or compile flags are given. `tes3x patch` then applies the selected patches to
a copy of the XBE. An engine whose required sites cannot be found stops with an error.
Byte-pattern patches locate their sites by content.
`--title` and a save pool also patch the
launcher, `Default.xbe`; a save pool gives the build its own saves (see `save_pool` in the
[configuration reference](configuration.md#profile)).

When a payload source must be compiled, the pipeline checks for LLVM before copying anything.
`clang not found` means LLVM is not installed or `paths.llvm` points elsewhere. A
patch that cannot find its site fails with the number of matches it found, which means the XBE is
not the retail GOTY build the patch expects.

## 5. Stage the complete game

The packed `Data Files`, the patched XBEs, `Morrowind.ini` with the profile's `[ini]` settings,
and the rest of the retail game folder are assembled into `<build_root>/<profile name>/deploy`.
With a title set, dashboard metadata is written too. The game folder carries its
[build manifest](deployment.md#build-manifest), `tes3xbuild.json`; the build folder also holds
`.tes3x-pipeline.json`, a record of what was built from what.
The manifest records the source profile's name and file hash. Content libraries include that
record in each published revision.

The pipeline only overwrites an empty folder or one it built before (`existing output is not owned
by tes3x_pipeline` otherwise). A build is assembled beside the output and moved into place when it
is complete; if a stage fails, what it produced so far is kept and the path is printed. Set
`paths.hardlink_retail = true`, or pass `--hardlink`, to hardlink unchanged retail files instead
of copying them; where the build and the retail folder are on different volumes they are copied.

## 6. Play, test, preview or deploy

- The GUI's **Play** action runs the profile in xemu with its persistent saves; see [xemu](xemu.md).
- `tes3x test` runs a scripted smoke test of the same profile in xemu; see
  [testing](testing.md).
- `--dry-run` lists what is in the Xbox folder and what an upload would change.
- `--deploy` uploads the build, over FTP or with `--deploy-agent` through the Dash;
  see [deployment](deployment.md).

You can also copy `<build_root>/<profile name>/deploy` to the Xbox with any FTP client.

## Command-line overrides

Command-line values override the profile:

```powershell
tes3x pipeline profiles/my-build.toml --preset testing --enable video-arena
tes3x pipeline profiles/my-build.toml --ini-set "General:Show FPS=1"
tes3x pipeline profiles/my-build.toml --package-mode loose
```

`--help` lists every option, including the instrumentation options described in
[diagnostics](diagnostics.md).

`--target NAME` selects one configured machine. Without it, builds validate FATX paths against the
longest configured Xbox `games_root`, so a build accepted for one target will not silently exceed
the path limit on another. With it, only that target's destination is checked.
