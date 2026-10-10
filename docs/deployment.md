# Deployment

A deploy copies a finished build to the install folder on a modded Xbox. It goes over FTP, which
most modded dashboards serve, or through the TES3X [console manager](#the-console-manager), which
is faster and leaves the old build in place until the new one has fully arrived.

## Deploying a build

In the GUI, select an Xbox target and choose **Actions > Deploy to Xbox…**, or press Deploy on
the toolbar. It asks before replacing a folder that holds something else. On the command line:

```powershell
tes3x pipeline profiles/my-build.toml --dry-run
tes3x pipeline profiles/my-build.toml --deploy
```

Run `--dry-run` first: it lists what is already in the Xbox folder and what an upload would change,
without changing anything. `--deploy` builds the profile and uploads it with
`tes3x deploy`, which can also send a folder that is already built.

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
belongs to another title or pool. `--replace-remote` (or `tes3x deploy --replace`) goes ahead;
the GUI asks first.

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

## Free space

When the console manager or the console add-on's agent is answering, a deploy asks it how much
space is free on the target drive and compares that with what the deploy adds: each uploaded file
in whole 16 KB clusters, less the files it replaces or deletes. It stops without changing
anything, with exit status 4, when the deploy will not fit, and warns when less than 256 MB would
be left. `--ignore-space` (on `tes3x pipeline` or `tes3x deploy`) goes ahead; the GUI asks
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
tes3x pipeline profiles/my-build.toml --deploy --verify-deploy size --discard-build
tes3x pipeline profiles/my-build.toml --deploy --verify-deploy hash --discard-build
```

`size` lists the uploaded files again. `hash` downloads every file again and compares it, which
roughly doubles the transfer. With `--discard-build`, the local build is deleted only once the
check passes. GUI deploys use size verification, so a completed deploy does not rely only on the
FTP upload command having returned successfully.

## Shared retail base

Several builds on one Xbox can share one clean copy of the retail game, so each build's folder
holds only what it changes. Set `profile.install_layout = "overlay"` to keep only files that
differ from retail in a build's game folder. Set `retail_root` on the Xbox target to a separate clean folder such as
`F:/Games/MorrowindRetail`. The pipeline enables the
[game folder overlay](../patches/data-overlay.md), writes its INI setting and removes unchanged
retail files automatically.

There is no implicit retail-base path: an overlay build or deploy stops when `retail_root` is
unset. The GUI suggests `MorrowindRetail` beneath the target's games root, but requires the path to
be set explicitly.

An overlay deploy checks that the shared base already matches the local clean game before changing
the profile folder. Install or synchronize it explicitly with:

```powershell
tes3x pipeline profiles/my-build.toml --deploy --install-retail-base
```

The GUI does this after showing the shared-base path in its deployment confirmation. The base
carries the retail `morrowind.xbe` and `Default.xbe`, which the console manager rebuilds a build's
XBEs from; the launcher also makes the base a dashboard entry that plays vanilla Morrowind. Never
point `retail_root` at a working or modded installation: synchronizing the base makes that folder
match the clean retail data. An overlay profile's `Default.xbe` is the patched engine, not the
retail launcher, so starting its dashboard entry can activate the overlay before any base file is
read.

## The console manager

The console manager is a TES3X program on the Xbox, started from the dashboard like a game. It
receives deploys from the PC, keeps several builds and installs a build from a multiplayer server
with no PC at all. A multiplayer build's main menu also offers it.

### Installing the console manager

In the GUI, choose **Actions > Install console manager…**, or **Install / update** under
**Console manager** on the **Target** workspace's **Overview** tab, which also shows the installed
version beside the one this PC would install. On the command line:

```
tes3x manager install [--target NAME] [--agent] [--dry-run]
```

Manager installation stages files in the TES3X data folder's `build/manager/install/`;
`--out FOLDER` chooses another staging folder. Source builds of the manager and its launcher
also go under the data folder's `build/manager/`.

It installs or updates the manager in `TES3XManager` under the target's `games_root` (`--folder`
changes the name), where the dashboard lists it. It also names the manager and this PC in the
console's [settings](#console-settings), so the manager pairs with this PC without a game deploy
first (`--no-agent` leaves the PC setting as it is) and a multiplayer build's main menu gets a
**Manager** entry.

The first install goes over FTP. Once the manager runs, `--agent`, or the GUI when the manager is
paired, updates it through the manager itself, and the new version starts the next time it is
launched.

A portable folder carries the manager. With pipx, download `manager.xbe` and `launcher.xbe` from
the [releases page](https://github.com/stjiub/tes3x/releases) and pass them as
`--xbe manager.xbe --launcher launcher.xbe`. In a checkout, TES3X builds them with nxdk
(`paths.nxdk`) when neither is given. `tes3x manager stage OUT` writes the folder without sending
it.

The folder holds a small launcher as `default.xbe`, the manager in `a\` or `b\`, the dashboard's
`_resources\default.xml` and a [build manifest](#build-manifest) of layout `manager`, which the
manager leaves out of its own build list. Installing again puts the manager back in `a\`.

### Through the console manager

`--deploy-agent` (or `tes3x deploy --agent`) deploys through the TES3X console manager
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

### From a server

A multiplayer server started with `--build` ([Handing out the
build](multiplayer-server.md#handing-out-the-build)) hands its build to the console manager, so players
need no PC. The manager's **Servers** tab lists the servers this console knows from each save pool's
`U:\TES3X\servers.ini`, the ones joined from the game's main menu, with each one's key fingerprint
and installed build. **Add server** types one in on the on-screen keyboard, by name or address with
`:port` when it is not 26500; **Password** on a server's page sets the password kept for it, and a
server that refuses for want of one asks for it and then carries on. On a server, **Update build**
installs or updates it and **Join** starts it, joining that server with a new game, as Join in the
game's menu does. Press **Start** on the Builds or Servers list to launch or join the highlighted
entry without opening its page, and **Back** to delete it: a build's folder is removed, a server's
keys and password are forgotten (a build installed from it stays). Both ask first. A server that
answers shows the character this console last played there.

An update asks the server through the game's encrypted session, with this console's key for that
server (made and kept in `servers.ini` if it has none, and the server's key pinned on first
contact, as the game does) and the password the game keeps there. The server answers with its
manifest's hash and serves the files over HTTP. Then, for each file the manifest lists:

- one already present with the same SHA-256 is kept;
- an XBE is rebuilt from its delta against the retail base's XBE with the recipe's retail digest;
- a retail file is copied from the retail base;
- anything else, or a rebuild or copy that fails, is downloaded.

Every file arrives under a `~t3x` staging name and is checked against the manifest; only when all
have arrived are they renamed into place, the replaced XBEs kept as `.prev`, so a failed or
cancelled update leaves the installed build as it was. Files the old manifest listed and the new
one drops are deleted; files no manifest listed are left alone. Plugins get modified times in the
manifest's load order, and `tes3xbuild.json` is written last, with `server` naming the server it
came from. The build goes into the folder it was installed into before, else the manifest's
`folder` under the first `Games` folder on F, G, E or C; a folder there that holds files but no
build manifest is replaced only after the manager asks.

The retail base set under **Settings > Retail base** supplies the XBEs and retail files, so an
overlay build is a download of its own files only.

### Signed updates

The manager updates itself from a signed release, which needs no PC trust beyond the release key.
It fetches releases itself from **Settings > Manager**, reading `release.json`,
`release.json.sig`, `manager.xbe` and `launcher.xbe` from the update feed,
`https://github.com/stjiub/tes3x/releases/latest/download/`. A release downloaded to the PC can be
sent instead:

```
tes3x manager update RELEASE [--agent] [--target NAME]
```

checks the release, sends it to `E:\TES3X\update` and, through the agent, restarts the manager. A
release can also be copied there by FTP or USB. Making a release is in
[development](development.md#releases).

`[Xbox] UpdateFeed` in `E:\TES3X\console.ini` points a console at another feed, any `http://` or
`https://` folder holding those files. The
manager uses the network settings in `console.ini` (`NetAddress`, `NetGateway`, `NetDns`) or,
without them, the dashboard's. It speaks TLS 1.2 and does not check certificates, since the
console's clock is often wrong; a release is trusted by its signature alone, so a feed or
network that is not trusted can withhold an update but not change one.

On start, and after fetching one, the manager checks a release in `E:\TES3X\update` against the
public key built into it (`keys/release.pub`), refuses one that is unsigned, signed by another key,
or not newer than itself, and deletes it. Otherwise it writes the new manager into its other slot
(`a\` or `b\`), replaces the launcher if the release's differs, marks the slot pending in
`slots.ini` and restarts. The launcher starts a pending slot at most twice; the new manager marks
itself good once it reaches its screen, and after two starts that did not, the launcher goes back to
the previous slot and the manager says the update did not start. A release whose files are still
arriving is left for the next start.

## Getting files back

`tes3x fetch` copies files or folders off the Xbox, such as saves or logs;
`tes3x diag pull` fetches and summarises the diagnostics log. `T:` and `U:` are per-title
drives that a dashboard's FTP server may refuse; their contents are under `E:/TDATA/<title ID>` and
`E:/UDATA/<title ID>`.

## Console settings

Settings that belong to a console rather than a build live outside every game folder, in
`E:\TES3X\console.ini`: `NetAgent` (the PC the console pairs with), `OverlayBase` (the retail base
on this console), `Manager` (the console manager's XBE, written when it is installed) and the
network keys `NetAddress`, `NetGateway` and `NetDns`. The game reads each
`[Xbox]` key there before `Morrowind.ini`, so one build's files are the same on every console and
its manifest hashes hold everywhere.

The pipeline writes these keys, whether they come from the profile, `--ini-set` or the target, to
`console.ini` beside `deploy/` instead of into `Morrowind.ini`. Deploy merges them into the
console's file (`tes3x deploy --console-ini FILE`), keeping the keys it does not set. The xemu
runner puts them back into the disc's `Morrowind.ini`, since an xemu disc belongs to one run.

## Build manifest

Every game folder the pipeline stages carries `tes3xbuild.json`, which says what the build is:

- `build`, a SHA-256 over the files' hashes and the load order, first in the file. Deploys and
  installs leave it as the pipeline wrote it, so a server compares it to tell a console whose
  build is out of date; a deploy of a hand-assembled folder computes it from the files sent;
- the profile, where it came from (the TES3X revision and the profile's SHA-256) and the install
  layout;
- every file, with its size, SHA-256 and origin: `xbe`, `retail` for a byte copy of a retail
  file, or `build` for everything else. A server decides from the origin what it hands out;
  players normally rebuild XBEs from their own retail copy;
- the plugins in load order, the `Morrowind.ini` settings the build changed and the save pool;
- for each XBE, the retail XBE it was made from, by its retail digest, the patches applied,
  the result's SHA-256 and the delta that rebuilds it;
- `folder`, the install folder's name, where the console manager puts the build.

A retail digest is the SHA-256 of an XBE with the edits scene copies carry made to it: the
certificate opened to any media and region, and the game's drive letters set to `D:`. A retail
copy and a scene copy of the same image therefore share one digest.
`tes3x patch XBE --digest` prints it.

The delta is a zstd frame, as `zstd --patch-from` makes, that turns the retail XBE with those
edits made into the patched one: about 90 KB for the engine. The pipeline writes it to
`<build_root>/<profile name>/deltas/<its SHA-256>.zst`, beside the game folder rather than in it,
and checks that it rebuilds the XBE. Deltas need the `zstandard` package
(`pip install zstandard`); without it the pipeline builds as before and leaves them out.

Deploy compares the console's copy of the manifest with the build, sends what differs and writes
the manifest last, with the time of the deploy, so it describes what the console holds. A folder
deployed before the manifest existed has `tes3xdeploy.json` instead; deploy still trusts its
SHA-1s and replaces it. `tes3x manifest FOLDER` checks a local game folder
against its manifest.
