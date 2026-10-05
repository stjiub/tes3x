# Deployment

`--deploy` uploads a finished build to the Xbox over FTP with `tools/tes3x_deploy.py`. You need a
softmodded or hardmodded Xbox running an FTP server, such as the one in most modded dashboards.

```powershell
python tools/tes3x_pipeline.py profiles/my-build.toml --dry-run
python tools/tes3x_pipeline.py profiles/my-build.toml --deploy
```

Run `--dry-run` first: it lists what is already in the Xbox folder and what an upload would change,
without changing anything.

## Where it goes

An Xbox target supplies a `games_root`, such as `F:/Games`, and the profile supplies an
`install_dir`, such as `MorrowindTest`. TES3X deploys to `F:/Games/MorrowindTest`, not its
`Data Files` folder. `profile.name` is the default install folder. Select a target with
`--target NAME`; an explicit destination option still wins where a command provides one.

Legacy `[deploy] remote_root` and `profile.remote_root` settings remain readable. TES3X splits the
local destination into a games root and folder, and uses the profile destination's final component
as its folder.

A deploy makes that folder match the build: files there that are not in the build are deleted.
Saves and the dashboard's `_resources` folder are not touched. Give every build its own folder, and
never deploy over an install you want to keep.

Deploy stops without changing anything, with exit status 3, when the folder has files but no
[build manifest](#build-manifest), holds another profile, or when the profile's save pool folder
belongs to another title or pool. `--replace-remote` (or `tes3x_deploy.py --replace`) goes ahead;
the GUI asks first.

## Build manifest

Every game folder the pipeline stages carries `tes3xbuild.json`, which says what the build is:

- the profile, where it came from (the TES3X revision and the profile's SHA-256) and the install
  layout;
- every file, with its size, SHA-256 and whether it may be handed out by a server. XBEs and
  byte copies of retail files may not: players rebuild those from their own retail copy;
- the plugins in load order, the `Morrowind.ini` settings the build changed and the save pool;
- for each XBE, the retail XBE it was made from, by its retail digest, the patches applied,
  the result's SHA-256 and the delta that rebuilds it.

A retail digest is the SHA-256 of an XBE with the edits scene copies carry made to it: the
certificate opened to any media and region, and the game's drive letters set to `D:`. A retail
copy and a scene copy of the same image therefore share one digest.
`python tools/tes3x_patch.py XBE --digest` prints it.

The delta is a zstd frame, as `zstd --patch-from` makes, that turns the retail XBE with those
edits made into the patched one: about 90 KB for the engine. The pipeline writes it to
`<build_root>/<profile name>/deltas/<its SHA-256>.zst`, beside the game folder rather than in it,
and checks that it rebuilds the XBE. Deltas need the `zstandard` package
(`pip install zstandard`); without it the pipeline builds as before and leaves them out.

Deploy compares the console's copy of the manifest with the build, sends what differs and writes
the manifest last, with the time of the deploy, so it describes what the console holds. A folder
deployed before the manifest existed has `tes3xdeploy.json` instead; deploy still trusts its
SHA-1s and replaces it. `python tools/tes3x_manifest.py FOLDER` checks a local game folder
against its manifest.

## Connecting

The Xbox's address is `targets.NAME.host` in `tes3x.local.toml`. The login comes from command-line
options, then the selected target, then the default `xbox`/`xbox`. Set
`TES3X_FTP_PASSWORD` or pass `--ask-password` to keep the password out of the config file.

## What a deploy does

- **Uploads only what changed.** Files are compared with the manifest, and only new or changed
  files are sent. The deploy prints each file and its within-file and total progress. A transient
  FTP failure reconnects and retries that file twice by default; `--retries N` changes the count.
- **Stamps plugin load order.** The engine orders plugins by file time, and FATX keeps times to 2
  seconds, so bulk copies tie. Deploy sets each plugin's time explicitly when the FTP server
  supports `MFMT`, and otherwise uploads plugins in order, paced apart. When any plugin changes,
  all plugins are sent again so the order is stamped as a whole.
- **Prepares the save pool.** A build with its own [save title ID](../patches/title-id.md) needs its
  save folder in place before it first runs; deploy writes the folder's title metadata and image.
- **Clears the cache.** With `rules.clear_cache_partitions = true` (off by default, on in the
  example profile), deploy empties the `X:`, `Y:` and `Z:` cache partitions, which would
  otherwise serve stale copies of changed assets.

## Shared retail base

Set `profile.install_layout = "overlay"` to keep only files that differ from retail in a build's
game folder. Set `retail_root` on the Xbox target to a separate clean folder such as
`F:/Games/MorrowindRetail`. The pipeline enables the
[game folder overlay](../patches/data-overlay.md), writes its INI setting and removes unchanged
retail files automatically.

There is no implicit retail-base path: an overlay build or deploy stops when `retail_root` is
unset. The GUI suggests `MorrowindRetail` beneath the target's games root, but requires the path to
be set explicitly.

An overlay deploy checks that the shared base already matches the local clean game before changing
the profile folder. Install or synchronize it explicitly with:

```powershell
python tools/tes3x_pipeline.py profiles/my-build.toml --deploy --install-retail-base
```

The GUI does this after showing the shared-base path in its deployment confirmation. The base does
not carry either XBE or dashboard metadata, so it is not another launchable game. Never point
`retail_root` at a working or modded installation: synchronizing the base makes that folder match
the clean retail data. An overlay profile's `Default.xbe` is the patched engine, not the retail
launcher, so starting its dashboard entry can activate the overlay before any base file is read.

## Free space

When the console add-on's agent is installed and answering, a deploy asks it how much space is
free on the target drive and compares that with what the deploy adds: each uploaded file in whole
16 KB clusters, less the files it replaces or deletes. It stops without changing anything, with
exit status 4, when the deploy will not fit, and warns when less than 256 MB would be left.
`--ignore-space` (on `tes3x_pipeline.py` or `tes3x_deploy.py`) goes ahead; the GUI asks first.
Without the agent the deploy says the free space is unknown and carries on.

## Limits

FATX limits each file or folder name to 42 characters and a full path to 250 (not counting the
drive letter). The build checks both before anything is uploaded. Names are never shortened
automatically, since plugins and meshes refer to files by name. A shorter `games_root` helps with
long paths but not with a single name that is too long. Names inside a BSA don't count.

A plugin name may hold only one dot; the Xbox skips `Mod V1.6.esp`. Packing renames such plugins
and the master references to them; see [ordering plugins](pipeline.md#3-order-plugins-and-pack).

## Verifying and discarding the build

```powershell
python tools/tes3x_pipeline.py profiles/my-build.toml --deploy --verify-deploy size --discard-build
python tools/tes3x_pipeline.py profiles/my-build.toml --deploy --verify-deploy hash --discard-build
```

`size` lists the uploaded files again. `hash` downloads every file again and compares it, which
roughly doubles the transfer. With `--discard-build`, the local build is deleted only once the
check passes. GUI deploys use size verification, so a completed deploy does not rely only on the
FTP upload command having returned successfully.

## Getting files back

`tools/tes3x_fetch.py` copies files or folders off the Xbox, such as saves or logs;
`tools/tes3x_diag.py pull` fetches and summarises the diagnostics log. `T:` and `U:` are per-title
drives that a dashboard's FTP server may refuse; their contents are under `E:/TDATA/<title ID>` and
`E:/UDATA/<title ID>`.
