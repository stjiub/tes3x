# Game folder overlay

Patch key: `data-overlay`

A modded build normally carries a full copy of the game: every retail file goes into its folder
beside the mod files, over a gigabyte for each build. With this patch a build folder holds only
what differs from retail, and reads everything else from an untouched install already on the
console. Each build is still its own folder with its own `default.xbe`, so dashboards list it as a
separate game. For an overlay build, `default.xbe` is a second copy of the patched engine rather
than the retail launcher: the launcher cannot read from the data-only base before the engine's
overlay hook is active.

See the [patch table](../docs/patches.md) for availability and selection.

## How it works

The engine reaches the kernel's file functions only through the XBE's import thunks. At startup
the patch replaces five of them with its own:

- `NtCreateFile`, `NtOpenFile` and `NtQueryFullAttributesFile`: when a read-only open or attribute
  query of a `D:\` path finds nothing, it is retried under the base folder. Opens that write,
  create or delete, and every path on another drive, are passed through unchanged, so saves and
  logs never leave the build.
- `NtQueryDirectoryFile` and `NtClose`: when a directory exists in both folders, a listing returns
  the build folder's entries first, then the base folder's, skipping names the build folder also
  has. Music, plugins and loose files from both folders are therefore visible to the engine.

A file in the build folder always wins over the same file in the base folder, archives included.

## Using it

Set `profile.install_layout = "overlay"`; the pipeline enables this patch and keeps the engine as
both `default.xbe` and `morrowind.xbe`, plus `Morrowind.ini`, dashboard files, and every file that
differs from the base. Plugins, mod archives
with `tes3xarch.txt`, and loose files therefore stay in the build. A file identical to the clean
retail source is left out. See [deployment](../docs/deployment.md#shared-retail-base) for installing
and verifying the shared base.

`E:\tes3xlog.txt` shows where the engine's files came from:

- `overlay.on` once the base folder is found, with `overlay.base` its device path;
  `overlay.off`, `overlay.bad_base` or `overlay.base_missing` when the patch stays inactive.
- `overlay.own` counts files the build folder served itself, and `overlay.own_path` names the first
  ones under `Data Files`. `overlay.create`, `overlay.open` and `overlay.attrs` count lookups that
  fell back to the base folder, and `overlay.path` names the first ones.
- `overlay.dir` counts directories listed from both folders, and `overlay.dup` the base entries a
  listing skipped because the build folder has them too.
- `overlay.bsaN` names each archive the engine opens, with its folder, and `overlay.bsaN.reads`
  counts reads from it, so a mod archive with reads is one the engine takes assets from.

Counters are logged at each power of two.

## Configuration

For an overlay-layout deployment, `deploy.retail_root` in the local config names the base folder
and deploy writes `[Xbox] OverlayBase` to the console's `E:\TES3X\console.ini` automatically. The value may be a drive path
(`F:\Games\MorrowindRetail`; drives C, E, F and G) or a device path
(`\Device\Harddisk0\Partition6\Games\...`). The console manager offers the retail bases it finds
on first start and writes the one chosen there too. A direct patch user may still set `OverlayBase`
manually. Without it, or when the folder does not exist, the patch does nothing.

xemu builds place their clean base under the disc's `Base` folder and substitute that path for the
run; the console-specific path is not needed there.

## Compatibility and limits

- The base folder is never written. A file the engine opens for writing must already be in the
  build folder, or it is created there.
- Every lookup that finds nothing in the build folder is made a second time in the base folder.
- At most eight merged directory listings can be open at once; a ninth lists only the build
  folder.
