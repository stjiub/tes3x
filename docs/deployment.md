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
- every file, with its size, SHA-256 and origin: `xbe`, `retail` for a byte copy of a retail
  file, or `build` for everything else. A server decides from the origin what it hands out;
  players normally rebuild XBEs from their own retail copy;
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

## Console settings

Settings that belong to a console rather than a build live outside every game folder, in
`E:\TES3X\console.ini`: `NetAgent` (the PC the console pairs with), `OverlayBase` (the retail base
on this console), `Manager` (the console manager's XBE, written when it is installed) and the
network keys `NetAddress`, `NetGateway` and `NetDns`. The game reads each
`[Xbox]` key there before `Morrowind.ini`, so one build's files are the same on every console and
its manifest hashes hold everywhere.

The pipeline writes these keys, whether they come from the profile, `--ini-set` or the target, to
`console.ini` beside `deploy/` instead of into `Morrowind.ini`. Deploy merges them into the
console's file (`tes3x_deploy.py --console-ini FILE`), keeping the keys it does not set. The xemu
runner puts them back into the disc's `Morrowind.ini`, since an xemu disc belongs to one run.

## Installing the console manager

```
python tools/tes3x_manager.py install [--target NAME] [--agent] [--dry-run]
```

installs or updates the manager in `TES3XManager` under the target's `games_root` (`--folder`
changes the name). The folder gets `default.xbe`, `_resources\default.xml` for the dashboard's
list and a build manifest of layout `manager`, which the manager leaves out of its own build list;
`E:\TES3X\console.ini` gets `Manager`, the manager's path, which puts a **Manager** entry on a
multiplayer build's main menu. The first install goes
over FTP; once the manager runs, `--agent` updates it through its own agent, and the new version
starts the next time it is launched. The XBE comes from `--xbe`, the copy a portable folder ships,
or an nxdk build of `manager/` (`paths.nxdk`). `tes3x_manager.py stage OUT` writes the folder
without sending it. In the GUI it is **Actions > Install console manager**, which goes through the
manager when it is paired.

## Through the console manager

`--deploy-agent` (or `tes3x_deploy.py --agent`) deploys through the TES3X console manager
instead of FTP. Start the manager on the Xbox with `E:\TES3X\console.ini` naming this PC:

```ini
[Xbox]
NetAgent=192.0.2.7:26501#FINGERPRINT
```

The fingerprint is this PC's agent key, `tes3x.agent.key` beside the local config, which the deploy
prints while it waits for the manager to pair. `--agent-port` changes the port deploy listens on,
for when the GUI holds 26501.

The GUI uses the manager by itself: when the manager is paired with the GUI, the target shows
**Manager** and Deploy goes through it. The GUI hands its port to the deploy for the duration;
the manager pairs with the deploy, then with the GUI again, about 6 seconds each way.

When the folder already holds files, each file is written under a temporary `~t3x` name and
renamed into place only once every file has arrived, so an interrupted deploy leaves the build as
it was; the next deploy deletes the leftovers. Into an empty folder there is no build to keep, so
files are written in place: a rename in a folder of a few hundred files is slow on FATX. Each
file's time is set once it is in place, plugins included, so load order holds. The manager reports
free space itself; it needs room for the new copies before the old ones go.

A full build of about 1 GB and 7,300 files takes about 7 minutes through the manager on wired
Ethernet, and about 13 minutes over the dashboard's FTP server.

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

The GUI does this after showing the shared-base path in its deployment confirmation. The base
carries the retail `morrowind.xbe`, which the console manager rebuilds a build's XBE from, but not
`Default.xbe` or dashboard metadata, so it is not another launchable game. Never point
`retail_root` at a working or modded installation: synchronizing the base makes that folder match
the clean retail data. An overlay profile's `Default.xbe` is the patched engine, not the retail
launcher, so starting its dashboard entry can activate the overlay before any base file is read.

## Free space

When the console manager or the console add-on's agent is answering, a deploy asks it how much
space is free on the target drive and compares that with what the deploy adds: each uploaded file
in whole 16 KB clusters, less the files it replaces or deletes. It stops without changing
anything, with exit status 4, when the deploy will not fit, and warns when less than 256 MB would
be left. `--ignore-space` (on `tes3x_pipeline.py` or `tes3x_deploy.py`) goes ahead; the GUI asks
first. Without either agent the deploy says the free space is unknown and carries on.

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
