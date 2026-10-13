# Changelog

No official releases yet. The first public release is in preparation.

## Unreleased

- New `boot-clear` patch: a build that starts the engine without the retail launcher no longer
  flashes green lines before its first splash. See [the patch](patches/boot-clear.md).
- New `dash-button` patch: a TES3X button in the main and pause menus that leaves for the Dash,
  in place of multiplayer's Dash button. The menu buttons are redrawn together so they match, and
  the menu is taller by a row. See [the patch](patches/dash-button.md).
- Builds whose engine starts without the retail launcher (`--direct-engine` runs and the overlay
  layout) now include `boot-clear`, `menu-still` and `dash-button` together, and multiplayer
  requires all three. Disable them to keep the retail menu and videos.
- New `menu-still` patch: the main menu of such a build shows a still of the retail logo movie's
  parchment and emblem instead of playing the movie and then a black screen. It needs `ffmpeg`
  on the `PATH` when building. See [the patch](patches/menu-still.md).

- The Dash can browse standalone content servers and install offered builds into its Builds list.
  The GUI's Content workspace can host chosen completed builds or always share every completed
  build under the build root. Installing the same entry again updates it from its latest revision.
  Content downloads use signed catalogs and the existing staged, checked installer. See
  [content servers](docs/content.md).
- The Content workspace now shows recent Dash install stages, progress, bytes sent and final
  results while its server runs. Update the Dash and content server together to see install results.
  See [content servers](docs/content.md).
- The Dash now saves an unacknowledged content install result and retries it when it next contacts
  that library, while its transfer ticket is valid. See [content servers](docs/content.md).
- Updating a published content build now serves its new files when its XBE delta has not changed.
- Retrying a content install now keeps one activity row across new download tickets, with the
  ticket count and total bytes sent. Update the Dash and content server together; the new ticket
  request requires a signed install attempt. See [content servers](docs/content.md).
- Content libraries now retain listed build revisions for selection in the Dash. The server can
  edit entry names and descriptions, withdraw revisions, and restore them. Update the Dash and
  server together; tickets now identify the selected manifest revision. See
  [content servers](docs/content.md).

- `tes3x game-tests` runs every game test against the retail engines named in the local
  config's new `[engines]` table, several at once, and prints what passed and failed. See
  [testing](docs/testing.md#every-game-test).
- `profile` targets can name a content locator, such as `profile=%chunk-read`, which finds the
  same function on NTSC and PAL engines. See the [profiler](patches/profile.md#using-it).
- The sampled PAL GOTY engine now resolves console and multiplayer bindings automatically;
  its virtual keyboard and server join work. Other retail engine revisions still need
  compatibility checks.
- `tes3x payload --portable-bindings` can build the supported diagnostics, console, multiplayer
  and `multi-bsa` sets once for the sampled NTSC and PAL GOTY engines; `tes3x patch` fills the
  engine addresses,
  original entry and kernel import slots from the user's XBE. Other payload source sets still
  need an image-specific build. See [payload injection](patches/payload.md).
- The pipeline uses a matching prebuilt payload for diagnostics, console and multiplayer, with
  or without `multi-bsa`; these builds no longer need a local compiler. Use `--build-payload` to
  compile from source. Other patch combinations still require LLVM. See [pipeline](docs/pipeline.md).
- The portable folder now carries checked prebuilt payloads instead of LLVM. Install LLVM
  separately for source builds or patch combinations outside the bundled sets.
- Payload sections and hook addresses can relocate within the same engine image. Existing
  fixed payloads still require their linked address; building from source still needs LLVM.
  See [payload injection](patches/payload.md).
- xemu disc additions and direct-engine boot break staging hardlinks before replacing files,
  preserving the source game folder when runs share a deploy tree.
- Quest scripts and the characters they move are shared in multiplayer: a script a conversation
  or quest starts is kept by the server with its progress and carries on in another player's
  game when you leave or after a restart, and a character moved by a script or a conversation
  is in the same place for every player, including players who join later. Update the server and
  install the build again. See
  [saving and character state](docs/multiplayer-client.md#saving-and-character-state).
- Follow, escort, travel and wander orders from conversations and quest scripts are shared in
  multiplayer: a companion keeps following the player who hired them in every player's game and
  after a server restart. Update the server and install the build again. See
  [saving and character state](docs/multiplayer-client.md#saving-and-character-state).
- The journal is shared in multiplayer: a quest one player moves on gives the other players the
  same entry, and the server keeps it in the world, so characters who join later or are new get
  it too. Joining no longer shows a "journal updated" message for each restored entry. Update the
  server and install the build again. See
  [saving and character state](docs/multiplayer-client.md#saving-and-character-state).
- The GUI's Server workspace keeps a list of worlds beside **Start**: make, duplicate, rename and
  delete them, each with its own settings, characters and world state, so the server always keeps
  them between runs. A **Characters** tab lists and deletes the selected world's characters.
  The worlds folder is under **Settings > Paths**. On first open the GUI makes `Default` from
  the earlier server settings and copies the earlier world folder into it, its server key
  included, so consoles keep trusting the server. See
  [Worlds in the GUI](docs/multiplayer-server.md#worlds-in-the-gui).
- Chat works: type `chat TEXT` in the console (or `tes3xnet say TEXT`), and every player sees
  `NAME: TEXT` on screen; the server prints it too. `msg NAME TEXT` sends to one player, and
  `msg admin TEXT` to the server's admin; quote a name with spaces. Admin `say` and `tell`
  messages now start with `Admin:`. Update the server, and install the build again for `chat`
  and `msg`. See [console commands](docs/multiplayer-client.md).

- The server page's player list has a Player column with each client's character name, and
  the server's `list` admin command prints it as `name NAME` after the address.
- The Dash no longer hashes a retail base again once the server's file list has verified it;
  it only checks that each file is there at its size. A different folder or list is still
  hashed. Delete the base's `tes3xbuild.json` to force a full check. Install the Dash again.
- Launching the Dash for xemu now selects its seeded retail base and installs the server's build
  to the xemu disk instead of staging a copy on the read-only disc.
- Building an overlay-layout profile for an xemu target no longer needs `retail_root`; Play uses
  the retail base staged on its disc.
- The GUI toolbar's main button is Deploy for an Xbox target and Play for an xemu target. Its
  menu holds Play on Xbox (with the dashboard agent add-on) and Launch Dash; the Build button's
  menu holds Check and Build.
- Installing and enabling a large mod such as Tamriel Data or Tamriel Rebuilt no longer freezes
  the GUI: unpacking, copying and scanning its files run in the background.
- The Dash's Settings tab has **Quit to dashboard**, which restarts the Xbox. Install the Dash
  again to get it.
- The console manager is now TES3X Dash. `tes3x dash` replaces `tes3x manager`, which still
  works, as do `tes3x release manager`, `--manager` and `--no-manager`. The Dash installs to
  `TES3XDash`, the Xbox's own settings live in `E:/TES3X/xbox.ini` (was `console.ini`) and the
  main menu entry reads `Dash`. Remove the old Dash and its `console.ini` from the Xbox, then
  install the Dash again; see [Deployment](docs/deployment.md#tes3x-dash).
- The `console` add-on is now `xbmc`: set `[addons] xbmc = true` in `tes3x.local.toml`.
- The Dash shows optional server names and descriptions plus current player counts.
  Set these with the server's `--name` and `--description` options or in the GUI.
- A changed server key shows both fingerprints and offers **Accept new key** or **Cancel**.
  Acceptance keeps the saved server, player identity and password.
- The build UI explains that Overlay layout shares retail files and enables `data-overlay`
  automatically; enabling the patch alone still builds a full game folder.

- Dash downloads reconnect after network interruptions and resume saved partial files on
  the next attempt, with SHA-256 verification and a download-speed indicator. Update the server
  for byte-range support; older servers restart the current file.
- Overlay installs check and complete a separate shared retail base before installing the build.
  Update both the Dash and the server, and configure the server's clean retail source with
  `--retail-base` or `paths.vanilla_root`. Downloads still follow the server's retail/XBE policy.
  See [server builds](docs/multiplayer-server.md#handing-out-the-build).

- Multiplayer shares learned dialogue topics and restores them when joining after a server restart.
  Update both server and game builds for gameplay protocol 23; existing character streams
  acquire topics on their next join. See [character state](docs/multiplayer-client.md#saving-and-character-state).
- Multiplayer keeps factions: rank and reputation are shared by all players, while membership
  and expulsion stay with each character, and all of them survive a server restart. Update both
  server and game builds; existing characters publish their factions on their next join. See
  [character state](docs/multiplayer-client.md#saving-and-character-state).
- Multiplayer keeps each character's own globals, such as vampirism, lycanthropy and guild
  expulsion counts, across leaving and server restarts; they are never shared with other
  players. Update both server and game builds; existing characters publish theirs on their next
  join. See [character state](docs/multiplayer-client.md#saving-and-character-state).
- `tes3xnet global NAME` prints a global's value to the console, so exec scripts can assert it.
- Multiplayer shares the world's globals, such as quest counters and rent flags: a change by
  one player reaches the others and survives a server restart. Update both server and game
  builds; values set before this update are not imported. See
  [character state](docs/multiplayer-client.md#saving-and-character-state).
- Multiplayer shares the variables that scripts keep on people and objects, such as quest
  progress: they reach the other players and survive a server restart. Update both server and
  game builds. `tes3xnet local ID NAME` prints one.
- Multiplayer keeps where people and creatures were and how hurt they were: after a server
  restart, or when someone enters a place nobody was in, they stand where they were left instead
  of where the game starts them. Their AI starts its current task again from there. Update both
  server and game builds. `tes3xnet actor 0xREFID` prints an actor's health.

- Multiplayer documentation has separate [player](docs/multiplayer-client.md) and
  [server admin](docs/multiplayer-server.md) guides.

- Patch promotion accepts maintainer-reviewed play and manual checks; scripted tests are optional.
  `validate check --gate` reports missing scripted evidence as warnings. The patch table shows
  availability without a game-test column. See [patch policy](docs/patch-policy.md).

- `tes3x init` creates the example config and profile from bundled files, keeping existing files.
- `build` and `library convert` honor `paths.mod_library` and accept `--config`.
- Texture/archive caches, Dash staging, dashboard backups and xemu runs default to the data
  folder. `scenario` uses normal config discovery; `xemu` and `scenario` accept `--work-root`.
  Move old run folders there, or select their old root explicitly when reusing them.
- The `testing` preset includes additional engine fixes. Autosaves, overlay support and the
  low-memory menu are visible in the GUI's default patch view. See the [patch table](docs/patches.md).
- Multiplayer state saves tolerate brief Windows file locks instead of stopping the server.
- Multiplayer join and remote admin password limits share tries across each IPv6 /64.
  Filling the bounded address table no longer resets existing limits; new sources wait
  until a slot recovers. Handshake limits use the same source grouping and bounded storage.
- Multiplayer servers reject unknown event kinds and malformed client payloads before
  applying or relaying them. See [accepted events](docs/multiplayer-server.md#accepted-client-events).
- The GUI includes **Help > About TES3X**, with the version, author, license and links to
  documentation, release notes, the project and issue reporting.
- `bsa`, `convert` and `qcow2` support `--help`. Texture conversion reports sizes without
  writing files; `qcow2` accepts an optional output path for conversion to raw.
- A normal multiplayer server shutdown returns success even when no clients joined.
- First-run guidance points to Settings for file paths and the Target workspace for Xbox
  and xemu setup.
- Settings shows folder defaults and automatic LLVM selection, with **Use default** actions.
  Custom mlox rules are under **Advanced**; normal use downloads rules automatically.
- The portable zip unpacks with Windows' built-in extractor; paths no longer exceed its limit.
