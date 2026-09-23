# Pipeline options

## Complete builds

`tes3x_pipeline.py` is the normal entry point. It composes the existing build,
hook, patch, pack and deploy tools; those tools remain independently usable.

Machine-specific paths belong in a local config (see `examples/local.toml`). Mod
selection and build policy remain in the profile:

```toml
[patches]
preset = "standard"                 # minimal, standard or development
enable = ["script-ext"]             # optional individual overrides
disable = []

[package]
mode = "delta-bsa"                  # or merged-bsa
archive_name = "tes3xmods.bsa"
archive_only = false
drive_letter = "D"

[ini]                               # optional Morrowind.ini keys, "SECTION:KEY" = value
"General:Show FPS" = 1
```

`--ini-set` on the command line is applied after `[ini]`, so it overrides a profile key.

A mod entry with `loose = true` ships every asset it wins as a loose file instead of packing
it. A retail asset it replaces is dropped from a merged archive, or listed in
`ArchiveInvalidationList.txt` in the game folder when the delta archive leaves retail unchanged.
Loose assets need `TryArchiveFirst=0`, which the build sets, and are incompatible with
`archive_only`.

`standard` admits verified default `core` and `correctness` patches.
`development` adds instrumentation and the console. `compat`, `performance`,
`qol` and `balance` remain explicit. Candidate or merely implemented catalogue
entries do not enter `standard`; they can still be explicitly enabled while
being tested.

Delta-BSA packaging derives the `multi-bsa` infrastructure patch. Any selected
hook derives the payload sources it needs, and the pipeline applies the payload
before dependent patches. A successful build carries the retail Xbox root payload
and stages `Default.xbe`, the patched `morrowind.xbe`, `Morrowind.ini` and generated
`Data Files` together. Disc-image and scene-release artifacts are excluded.

A profile without enabled mods skips collection and packing: the retail `Data Files` are staged
unchanged, `[ini]` keys are still applied, and no `multi-bsa` is derived.

```powershell
python tools/tes3x_pipeline.py examples/mods.toml --plan
python tools/tes3x_pipeline.py examples/mods.toml --dry-run
python tools/tes3x_pipeline.py examples/mods.toml --deploy
```

The default output is `BUILD_ROOT/PROFILE_NAME`. Only an empty directory or an
output carrying the pipeline marker can be replaced. Work from a failed stage is
kept and reported for inspection. The deploy command creates missing destination
directories; `--dry-run` reports uploads and orphan removals without applying
them.

## Xbox destination paths

Build and pack accept `--remote-root "F:/Games/MorrowindTest"`. Default:
`F:/Games/Morrowind`. The builder also accepts `[profile].remote_root` in TOML;
the CLI overrides it. The prefix is the game folder, not its Data Files folder.

Checks cover each component (42 characters) and the full destination path
(250 characters including separators, excluding the drive letter and colon).
Source: [FATXplorer's filesystem reference](https://fatxplorer.eaton-works.com/fatx-file-system-limitations-reference/).
The full-path boundary has unit coverage; it has not been probed on this console.

`tes3x_deploy --remote` remains required and is checked before FTP connection,
including dry-run. A different destination is therefore checked again at deploy
time.

Deploy keeps `tes3xdeploy.json` in the remote folder: each file's size and SHA-1
as last sent. A file is resent when its size or hash differs; without an entry,
same-size `.xbe`, `.ini`, `.txt` and `.xml` files are always resent. If any
plugin is sent, all plugins are resent in load order.

Build validates the prospective loose Data Files tree. Pack validates only actual
loose output paths, including `Morrowind.bsa`; names inside the BSA do not consume
FATX directory entries. No automatic renaming: references must be rewritten along
with names. Shortening the installation directory only fixes total-path overflow,
not a filename or directory component exceeding 42 characters.

## Reachability

```powershell
python tools/tes3x_build.py examples/mods.toml --prune `
  --vanilla "build/vanilla/Data Files" `
  --out build/pruned-tree --reachability-json build/reachability.json `
  --remote-root "F:/Games/MorrowindTest"
```

Use a new/empty output directory. Roots include all asset definitions in the
retail master and enabled plugins, vanilla archive replacements, voice directories,
and non-prunable/globbed content. Mesh references retain their textures, extension
alternatives and animation companions. BOOK images and literal script paths are
included. Overridden record definitions are intentionally retained as a conservative
superset. The unnamed retail BSA itself is not pruned.

Add `[rules].keep_assets = ["textures/custom_dynamic/*"]` for assets selected by
runtime conventions that do not appear as literal paths. Unknown NIF layouts keep
all textures and report a warning. The reader handles external NiSourceTexture
fields in NIF 4.0.0.2; it is not a complete engine dependency analysis.

The JSON lists each retained file's reasons, removed paths/bytes, unresolved
references, and warnings. Unresolved references include original retail references
and do not establish that those files are absent at runtime. Removing an asset
which was never loaded saves storage, not resident RAM.

## mlox and TES3Merge

Generate an initial built tree using `tes3x_build`. Run mlox against its plugins:

```powershell
python tools/tes3x_plugins.py order build/DataFiles `
  --vanilla "build/vanilla/Data Files" --tool build/vendor/mlox/mlox.exe `
  --rules build/vendor/mlox/mlox_base.txt --work build/mlox-work --out build/order.json
```

Work directories must be new. mlox runs with downloads disabled on copies,
with real plugin sizes/headers available to its rules. Keep `mlox.msg` alongside
the legacy executable. Order JSON records executable, rule database and plugin
SHA-256 hashes. This session used the installed 0.61-era executable with the
[maintainers' legacy rules](https://github.com/DanaePlays/mlox-rules/blob/main/mlox_base_legacy.txt),
whose embedded version is `2017-15-10 11:11:11 (UTC)`, not the modern rule syntax.

Pass `--load-order build/order.json --vanilla "..."` to `tes3x_build` to apply it.
All plugins must appear once, masters before ESPs, dependencies before users;
changed plugin bytes invalidate the order. Each plugin gets a unique timestamp,
four seconds apart from 2001-01-01 UTC. Asset conflict priority remains the profile's
mod order; mlox sorts plugins only.

```powershell
# The installed TES3Merge requires .NET 6. This session validated running it
# on the installed later runtime via explicit major-version roll-forward.
$env:DOTNET_ROLL_FORWARD = 'Major'
python tools/tes3x_plugins.py merge build/DataFiles `
  --vanilla "build/vanilla/Data Files" --tool "<TES3Merge.exe>" `
  --order build/order.json --work build/merge-work --out "build/merged/Merged Objects.esp"
```

TES3Merge generates an additional conflict-resolution patch, **not plugin
consolidation**. Keep the original plugins. [Upstream usage](https://github.com/NullCascade/TES3Merge).
The wrapper expands the two four-byte expansion stubs only in its PC-side tool
workspace. A filename marker and empty PC archive prevent discovery of the user's
other installs; neither ships. Default TES3Merge patches are enabled. Logs and
the generated patch's source hashes remain available for review.

```powershell
python tools/tes3x_pack.py build/pruned-tree --vanilla "build/vanilla/Data Files" `
  --ini build/vanilla/Morrowind.ini --out build/pipeline-deploy --archive-only `
  --merge-patch "build/merged/Merged Objects.esp" --remote-root "F:/Games/MorrowindTest"
```

Pack validates the patch's provenance, includes all source plugins, and stamps
the complete order including retail/stub masters. Its output must be new/empty.
Hash collisions now fail rather than silently dropping an asset. The staged tree
still needs the appropriate engine XBE/boot arrangement before use as an install.

## Map companions and invalidation

`python tools/tes3x_map.py "build/vanilla/Data Files/Morrowind.esm.map" --json build/map.json --preview build/tile.png`
inspects map records and decodes the first CMAP image. These are precomputed map
data, not plugin record indexes. Existing companions stay loose.

Pack's repeatable `--loose-asset "textures/example.dds"` stages a matching asset
both in the BSA and loose, then writes `ArchiveInvalidationList.txt` in the game
root. It sets `TryArchiveFirst=0`. It cannot be combined with `--archive-only`:
fallback for invalidated assets in that mode is unverified. No exceptions are
enabled by default. Each list entry is a relative backslash path with a newline,
including the last entry. No comments or blank lines. The format is established
from the retail reader; the new generated list has not yet been tested in-game.

## Audio and mesh checks

Build accepts `--sox build/vendor/sox/sox.exe --sound-rate 22050`. Conversion is
opt-in for mod WAVs only; channels are preserved, sample rates never increase,
bit depths are preserved up to PCM16, and output duration/format are checked.
The installed SoX 14.4.2 installer was extracted into the workspace without
installing it system-wide. Compressed WAVs are not handled by this converter.

`python tools/tes3x_assets.py build/pruned-tree --json build/assets.json` checks
NIF version/block-count bounds and external texture fields and inventories PCM
WAV formats. It does **not** validate geometry, skinning, controller links or GPU
limits and cannot certify a mesh against all engine crashes.

Sources for asset fields:
[OpenMW TES3 records](https://github.com/OpenMW/openmw/tree/master/components/esm3),
[Niftools schema](https://github.com/niftools/nifxml/blob/develop/nif.xml).
