# Packaging

The pipeline's packing stage turns the collected mod files and the retail `Data Files` into what
ships to the Xbox. The Xbox searches a folder entry by entry for every file it opens, and a missed
lookup scans the whole folder, so how files are packaged matters for load times as well as size.

## Package modes

`package.mode` in the profile, or `--package-mode`, picks one:

| mode | what ships | needs |
|---|---|---|
| `delta-bsa` (default) | retail `Morrowind.bsa` unchanged, plus the mod files in their own archive, `tes3xmods.bsa` | LLVM, for [multi-BSA loading](../patches/multi-bsa.md) |
| `merged-bsa` | mod files merged into a rebuilt `Morrowind.bsa` | nothing extra |
| `loose` | every mod file loose in `Data Files`, retail archive unchanged | nothing extra |

Both archive modes pack meshes, textures, icons, sounds and book art into an indexed archive, so
the Xbox does not have to search large folders for each file. Plugins, splash screens, video,
music and fonts always ship loose. `delta-bsa` leaves the retail archive alone, so rebuilds and
uploads are smaller. `package.archive_name` renames the mod archive.

A `loose` build is the easiest to inspect and avoids archive limits, such as two file names that
hash the same, but a large mod list puts over a thousand files in some folders.

## Loose files in an archive build

`loose = true` on a mod, or a pattern in `package.loose_assets`, keeps just those files loose in an
archive build. When a loose file replaces a retail asset, the retail copy is dropped from a merged
archive, or listed in `ArchiveInvalidationList.txt` when the retail archive is left alone. Loose
files need `TryArchiveFirst=0`, which the build sets, so they cannot be combined with
`package.archive_only`.

TES3 BSA entries are addressed by hash rather than full path. If selected assets share a hash,
the packer automatically keeps every member of that collision group loose so none are discarded.
This also enables loose-first lookup in the staged INI; no profile exception is needed.

Outside a profile, `tes3x pack TREE --vanilla DATA --out DIR --loose-asset "textures/example.dds"`
does the same: matching files ship loose instead of in the archive, with the same handling of
the retail copies they replace.

## Mod archives

A mod's own `.bsa` archives are unpacked into the build by default, so their files take part in
conflicts like any other. To ship an archive as it is, set `archives = "load"` on the mod, or in
the GUI right-click the mod and choose **Load archives with multi-bsa**. The pipeline lists such
archives in `tes3xarch.txt` and applies [multi-BSA loading](../patches/multi-bsa.md).

## Expansion master placeholders

Xbox GOTY keeps the expansion content in `Morrowind.esm`, while plugins still name `Tribunal.esm`
and `Bloodmoon.esm` as masters. For a modded build, TES3X copies either file when a mod or the
retail `Data Files` supplies it, and otherwise generates a four-byte file containing only `TES3`.
These placeholders satisfy the dependency names; they do not add or replace expansion content. A
profile with no mods stages the retail `Data Files` unchanged and does not generate them.

## Pruning unused assets

```powershell
tes3x build profiles/my-build.toml --prune --library "D:/Morrowind Mods" --vanilla "D:/Morrowind/Data Files" --out pruned --reachability-json reachability.json
```

`tes3x build` reads the library from the profile's `library` or `--library`, not from the local
config, and `--vanilla` is the clean game's `Data Files`.

`--prune` drops mod assets that nothing refers to. It follows references from records in the
masters and plugins, from meshes to their textures and animations, from books to their images and
from scripts to literal paths. When unsure it keeps the file. It only saves disk space, not memory,
since an asset nothing loads never used memory anyway.

Assets picked at runtime by name, rather than referenced directly, won't be found. Keep them with
`rules.keep_assets = ["textures/custom_dynamic/*"]`. The JSON report lists why each file was kept
and what was removed.

## Sound and mesh checks

`tes3x build --sox PATH --sound-rate 22050` resamples mod WAVs to at most that rate with
[SoX](https://sourceforge.net/projects/sox/). It never raises the sample rate and keeps the channel
count. Compressed WAVs are left alone.

`tes3x assets pruned --json assets.json` flags malformed NIF
headers and missing texture references, and lists WAV formats. It doesn't check geometry, skinning
or anything else that can crash the renderer.

`tes3x map "Data Files/Morrowind.esm.map"` dumps a map companion file and can save
the first map image with `--preview tile.png`. `tes3x audit "Data Files"` checks any
`Data Files` folder for long names, junk files and duplicates.
