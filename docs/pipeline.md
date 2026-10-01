# Pipeline

`tools/tes3x_pipeline.py` turns one profile into a complete game folder, and optionally deploys
it. It runs a fixed series of stages, each implemented by a tool you can also run by itself (see
[commands](commands.md)). Every profile and local-config key is in the
[configuration reference](configuration.md).

```text
   local config + profile + mod library
                  |
         1. resolve and check
                  |
       2. collect the winning files
                  |
    3. order plugins, pack Data Files
                  |
   4. build the payload, patch the XBE
                  |
      5. stage the complete game
                  |
     6. test, preview or deploy
```

```powershell
python tools/tes3x_pipeline.py profiles/my-build.toml --check
python tools/tes3x_pipeline.py profiles/my-build.toml
python tools/tes3x_pipeline.py profiles/my-build.toml --dry-run
python tools/tes3x_pipeline.py profiles/my-build.toml --deploy
```

## 1. Resolve and check

Reads `tes3x.local.toml` and the profile, resolves the preset and patch selection, the mods and the
package mode, and prints what would be built. `--check` stops here.

A profile with no mods is a patches-only build: stages 2 and 3 are skipped and the retail
`Data Files` ship unchanged. `[ini]` settings still apply.

Common failures:

- `set paths.vanilla_root in local config or pass --vanilla`: the local config does not name the
  clean game folder.
- `unknown selectable patches: ...`, or patches both enabled and disabled.
- `rules.plugin_order = 'mlox' needs mlox` or `needs paths.mlox_rules`: see
  [sorting plugins with mlox](#sorting-plugins-with-mlox).
- `deployment requires deploy.host, and profile.remote_root or deploy.remote_root`.

### Choosing engine fixes

The profile's `[patches] preset` selects a baseline:

| preset | contents |
|---|---|
| `minimal` | no optional engine fixes |
| `recommended` | release fixes selected for general use |
| `testing` | `recommended`, preview fixes, diagnostics and the console |

`enable` and `disable` add or remove individual patches, and `categories` adds every patch in a
category; the [example profile](../examples/profile.toml) lists them all. `--preset`, `--enable`
and `--disable` on the command line override the profile. Some patches are added when something
needs them: `delta-bsa` packing adds `multi-bsa`, `mwse-legacy` adds `script-ext`, and
`multiplayer` adds `diagnostics`. Every build gets [`boot-media`](../patches/boot-media.md) and
[`drive-letters`](../patches/drive-letters.md). See the [patch table](patches.md), the
[`[Xbox]` ini keys](ini-keys.md) patches read, or `python tools/tes3x_patch.py --list`.

## 2. Collect the winning files

`tes3x_build.py` stacks the profile's mods in order, later mods winning when two ship the same
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
`tes3x_plugins.py` does the ordering.

`tes3x_pack.py` then packages the tree with the retail files according to `package.mode`:
`delta-bsa` (the default), `merged-bsa` or `loose`. See [packaging](packaging.md).

### Sorting plugins with mlox

With `plugin_order = "mlox"` in the profile's `[rules]`, [mlox](https://github.com/mlox/mlox) sorts
plugins using the community's ordering rules. File conflicts between mods still go by mod order;
mlox only changes the plugin load order.

mlox isn't included with TES3X. To set it up:

1. `python -m pip install mlox`. Add `--no-deps` to skip its GUI's dependencies, which TES3X
   doesn't use.
2. Get the rules: **Download** next to "mlox rules" in the GUI's local settings, or
   `python tools/tes3x_plugins.py fetch-rules mlox/mlox_base.txt`, then set `paths.mlox_rules` to
   that file. They come from the [mlox-rules project](https://github.com/DanaePlays/mlox-rules)
   and change often, so download them again now and then.

mlox runs on a copy of the build's plugins and never touches your library. Its conflict and
missing-requirement warnings are printed during the build. Everything it said, including notes,
goes to `mlox-messages.txt` in the build folder, and the order it picked to `mlox-order.json`.
Many notes are advice for the PC version and don't apply to the Xbox.

## 4. Build the payload and patch the XBE

When a selected patch needs code, `tes3x_payload.py` compiles the [payload](../patches/payload.md)
for your `morrowind.xbe` with clang and lld-link. `tes3x_patch.py` then applies every selected
patch to a copy of the XBE, each located by content. `--title` and a save pool also patch the
launcher, `Default.xbe`.

This stage needs LLVM whenever a patch needs code; the pipeline checks for it before copying
anything. `clang not found` means LLVM is not installed or `paths.llvm` points elsewhere. A
patch that cannot find its site fails with the number of matches it found, which means the XBE is
not the retail GOTY build the patch expects.

## 5. Stage the complete game

The packed `Data Files`, the patched XBEs, `Morrowind.ini` with the profile's `[ini]` settings,
and the rest of the retail game folder are assembled into `<build_root>/<profile name>/deploy`.
With a title set, dashboard metadata is written too. The build folder also holds
`.tes3x-pipeline.json`, a record of what was built from what.

The pipeline only overwrites an empty folder or one it built before (`existing output is not owned
by tes3x_pipeline` otherwise). A build is assembled beside the output and moved into place when it
is complete; if a stage fails, what it produced so far is kept and the path is printed. Set
`paths.hardlink_retail = true` to hardlink unchanged retail files instead of copying them, when the
build and the retail folder are on the same NTFS volume.

## 6. Test, preview or deploy

- `tools/tes3x_test.py` boots the same profile in xemu; see [testing](testing.md).
- `--dry-run` lists what is in the Xbox folder and what an upload would change.
- `--deploy` uploads the build; see [deployment](deployment.md).

You can also copy `<build_root>/<profile name>/deploy` to the Xbox with any FTP client.

## Command-line overrides

Command-line values override the profile:

```powershell
python tools/tes3x_pipeline.py profiles/my-build.toml --preset testing --enable video-arena
python tools/tes3x_pipeline.py profiles/my-build.toml --ini-set "General:Show FPS=1"
python tools/tes3x_pipeline.py profiles/my-build.toml --package-mode loose
```

`--help` lists every option, including the instrumentation options described in
[diagnostics](diagnostics.md).
