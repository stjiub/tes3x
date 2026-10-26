# Changelog

No official releases yet. The first public release is in preparation.

## Unreleased

- Multiplayer server: `tes3x net hash-password` writes a salted hash for `--password-file` (or,
  with `--admin`, for `admin-password.txt`) so the server's files do not hold the password
  itself; plain files still work. A new `server.key` is created readable by its owner only, and
  the server warns on Linux when a key or password file is readable by other accounts. See
  [keys and passwords](docs/multiplayer-server.md#keys-and-passwords).
- Downloads are verified: `tes3x xemu-setup` refuses an xemu build whose SHA-256 differs from the
  one GitHub lists, and the portable folder's Python is pinned like its 7-Zip. Mod archives
  (`.zip`, `.7z`, `.rar`) that would write outside their folder are refused.
- GUI: the Content workspace is now the **Builds** tab of **Server**, beside **Multiplayer**.
  Its "Start content server with multiplayer server" option reads "Share builds when the
  multiplayer server starts". See [the GUI guide](docs/gui.md#server).
- Dash: the tabs are now **Library**, **Servers** and **Settings**. **Library** is the old
  **Builds** tab (the builds installed on the Xbox), and the **Content** tab is gone: content
  servers are entries on **Servers**, labelled *Multiplayer*, *Builds* or both. Adding an entry
  asks what the host offers; press A on a Builds entry to browse and install its builds. See
  [content servers](content.md#browse-on-the-xbox).
- New patch `pool-grow`: when the engine's small-block pools fill, it adds more instead of giving
  each further block a header in the heap. With the Tamriel Rebuilt masters on a 128 MB console
  4.4 MB more memory stays free. See [the patch notes](patches/pool-grow.md).
- New patch `link-name-arena`: NPCs' race, class, faction, head and hair names and inventory item
  IDs wait for linking in memory paged to disk instead of the engine heap, and NPC, creature and
  container records keep their inventories there. With the Tamriel Rebuilt masters on a 128 MB
  console 4.2 MB more memory stays free after loading, and 2.5 MB more in a new game. See
  [the patch notes](patches/link-name-arena.md).
- New patch `script-arena`: compiled script bytecode lives in memory paged to disk instead of the
  engine heap; it turns on `link-name-arena`. With the Tamriel Rebuilt masters 2.1 MB more memory
  stays free, at no cost to load time. See [the patch notes](patches/script-arena.md).
- New patch `path-arena`: the model and icon paths and display names of loaded records live in
  memory paged to disk instead of the engine heap; it turns on `link-name-arena`. With the Tamriel
  Rebuilt masters about 2.4 MB more memory stays free in a new game. See [the patch notes](patches/path-arena.md).
- New patch `land-arena`: the land objects of exterior cells live in memory paged to disk instead
  of the engine's land pool and heap; it turns on `link-name-arena`. With the Tamriel Rebuilt
  masters about 2.1 MB more memory stays free, with no change in load time. See
  [the patch notes](patches/land-arena.md).
- New patch `npc-arena`: the NPC records' objects live in memory paged to disk instead of the
  engine's NPC pool and heap; it turns on `link-name-arena`, `ref-index` and `soundgen-index`.
  With the Tamriel Rebuilt masters about 3 MB more memory stays free in a new game. See
  [the patch notes](patches/npc-arena.md).
- New patch `soundgen-index`: an animation's footstep and creature sound keys are matched against
  the loaded creatures instead of walking every loaded object once per key, at startup and when a
  creature's animation first loads. See [the patch notes](patches/soundgen-index.md).
- `video-arena`: `[Xbox] VideoMemoryKB` now also shrinks the texture and vertex buffer arena,
  down to 1,024 KB, on a 64 MB console as well as on 128 MB. It still grows the arena only with
  more than 64 MB. Textures and vertex buffers that do not fit in the arena are packed together
  instead of taking a 4 KB page each, which saves up to 11 MB in a busy cell when the arena is
  small. See [the patch notes](patches/video-arena.md).
- `lazy-dialogue` now also loads greetings, voice lines, persuasion replies and journal entries
  on first use, not only topics. With the Tamriel Rebuilt masters on a 128 MB console 1.4 MB
  more memory stays free in a new game. See [the patch notes](patches/lazy-dialogue.md).
- `info-name-arena` together with `lazy-dialogue` no longer restarts the game when the first
  conversation of a session opens.
- Multiplayer: your character's Mark spell place is kept by the server, so Recall still takes
  you there after a restart or a join from another console. A mark in a cell the build lacks is
  left unset. Rebuild to get it.
- Mod library: the GUI installs mods that ship only an `Animations` folder, such as Can't Touch
  This, so enabling it in a profile builds its dodge patch.
- Multiplayer: the pause menu while joined no longer flashes the stock menu before it changes. It
  has no New or Save; Exit leaves the server after saving to it, with a box that says so instead
  of "unsaved progress will be lost", and Load asks to leave and returns to the main menu with
  the server list open. The separate Save to Server and Leave buttons (Black and White) are gone.
- Multiplayer: the main menu has no New, since a game played alone is not part of multiplayer;
  Load opens the server list that Black used to open. White still starts the Dash.

- The main menu still is cleaner: it averages the first second of the menu video instead of
  one frame, so the picture no longer shows the video's compression blocks. Rebuild to get it.

- The GUI can run several commands at once, each in its own output tab. Deploying a built
  profile to a second Xbox no longer waits for the first upload to finish, and logs or saves on
  one target no longer wait for another's deploy. A profile still runs one build, check or
  deploy-with-build at a time.
  The status bar shows the progress of the selected tab, and conflict and space questions
  appear in the job's own tab instead of a pop-up.
- Play on a profile that changed since its build, while xemu is still running that build, offers
  to play the existing build instead of refusing.

- The GUI shows a deploy's upload as a progress bar in the status bar (size, speed, time left)
  instead of a log line a second. The command line output is unchanged.

- Multiplayer: a player in combat who presses Leave now waits `--combat-leave` seconds (default 10)
  before the save and quit begin, so leaving cannot end a fight. Taking damage cancels the leave,
  and leaving combat ends the wait early; `0` leaves at once. See
  [the server guide](docs/multiplayer-server.md).

- New `lazy-dialogue` patch loads topic responses only as their place in the plugins and builds a
  topic's responses when it is first asked, which frees about 12 MB at the first frame with the
  full Tamriel Rebuilt masters and lowers the loading peak by about 19 MB. A topic is built only
  when one of its responses could suit the speaker, so a character who knows many topics does not
  build them all in the first conversation. It turns on `dialogue-merge`. See
  [the patch](patches/lazy-dialogue.md).

- New `dialogue-owner` patch finds a response's topic directly instead of searching every topic,
  which result scripts, `Choice` conditions and newly heard responses do. See
  [the patch](patches/dialogue-owner.md).

- New `mcp-30` patch corrects bow projectile aim for scaled races and in third person. See
  [the patch](patches/mcp-30.md).

- New `topic-colors` patch dims dialogue topics whose current response you have already read.
  See [the patch](patches/topic-colors.md).

- New `mcp-118` patch keeps items dropped in third person while airborne near the player. See
  [the patch](patches/mcp-118.md).

- New `mcp-25` patch makes Blind reduce the player's attack chance instead of increasing it.
  See [the patch](patches/mcp-25.md).

- New `mcp-48` patch shows earned level-up multipliers when they bring an attribute to 100 and
  avoids overlapping x3 multiplier labels. See [the patch](patches/mcp-48.md).

- New `mcp-55` patch keeps a custom potion name after Create and groups inventory icons when
  alchemy closes. See [the patch](patches/mcp-55.md).

- New `mcp-149` patch prevents disposing an actor during its death animation, allowing death
  scripts and spell cleanup to finish. See [the patch](patches/mcp-149.md).

- New `mwse-enable-disable-vars` patch stops `Enable` and `Disable` crashing in a script that has
  no local variables. See [the patch](patches/mwse-enable-disable-vars.md).

- New `mcp-160` patch stops `RemoveItem` and `Drop` from lowering a character's encumbrance for
  items that were not in the inventory. See [the patch](patches/mcp-160.md).

- New `mwse-pathgrid-zero` patch stops Wander and Flee from crashing on a cell whose pathgrid has
  no nodes. See [the patch](patches/mwse-pathgrid-zero.md).

- New `mcp-6` patch lets Restore Attribute and expiring Drain Attribute recover a player's
  temporary Fortify Attribute bonus. See [the patch](patches/mcp-6.md).

- `tes3x mcp --dump` now works when temporary subdirectories cannot be written.

- New `mcp-127` patch keeps replacement land textures from blanking or cropping the world map.
  See [the patch](patches/mcp-127.md).

- `mwse-legacy`: `XPosition`, `XPositionCell`, `XAITravel` and `XGetSpellEffects` now hand their
  coordinates and spell to the game. They used stale values before, and could break a `While`
  loop in the calling script. See [legacy MWSE bytecode](patches/mwse-legacy.md).

- `tes3x scriptasm --command NAME` calls a script command by name instead of opcode. See
  [script opcode extensions](patches/script-ext.md#using-it).

- New `cant-touch-this` patch plays a dodge animation when a melee attack misses a calm NPC,
  using the clips from the Can't Touch This mod. The catalog selects it, and the animation merge,
  when the mod is enabled in a profile. Arrow and thrown-weapon misses do not animate yet. See
  [the patch](patches/cant-touch-this.md).

- Profiles can merge extra animation KFs into `xbase_anim.kf` with `animation.merge_kf`, including
  when the retail base is inside `Morrowind.bsa`. Install `tes3x[kf]` to use it. See
  [the pipeline guide](docs/pipeline.md#merging-animation-kfs).

- `multi-bsa` now gives later archives consistent priority when they replace a retail asset;
  previous file lookups could cause the stock copy to load instead. See [multiple BSA loading](patches/multi-bsa.md).

- New `mcp-132` patch makes scripted animations play across humanoid body layers, supports added bones and updates third person player animations. See [the patch](patches/mcp-132.md).

- New `mcp-173` patch saves NPC faction rank changes made by `RaiseRank` and `LowerRank`, so the
  rank survives loading a save. See [the patch](patches/mcp-173.md).

- Multiplayer servers now keep an NPC's attributes and skills, a creature's combat, magic and
  stealth, a merchant's barter gold and each actor's corpse timer, and share kill counts, so
  `GetDeadCount` quests agree between players and after a server restart. See [the server guide](docs/multiplayer-server.md).

- Multiplayer servers now keep the temporary magic effects on NPCs and creatures (a cast
  Fortify, Drain or Paralyze, a potion), so they survive a server restart and carry over when
  another player's console takes the actor over, and end on time there. Abilities, diseases and
  curses come from the actor's own record and bound items are not shared. See
  [the server guide](docs/multiplayer-server.md).

- Multiplayer journal updates now show the quest name and whether it started, advanced or
  completed in a text notification. Entries restored on join stay quiet. See
  [the player guide](docs/multiplayer-client.md).

- Enabled catalog mods now select their required patches automatically. Patch for Purists selects
  `dialogue-merge` to keep stock dialogue responses needed during character creation. See the
  [mod catalog](docs/catalog.md).

- New `mcp-47` patch keeps another actor's magic-item changes from deselecting the player's
  spell. See [the patch](patches/mcp-47.md).

- Builds can enable `extended-tooltips` for an in-world theft warning and more item information in
  menu tooltips: ownership, weapon speed and reach, projectile damage, enchantment type and
  charge, and filled soul gems. See [extended tooltips](patches/extended-tooltips.md).
- Profiles can set `ui.scale` to shrink text, the help strip, minimap, resource bars, equipment
  art, the options, level-up and character creation menus, message boxes, and common menu panels together. `ui.widescreen_480p`
  also corrects horizontal proportions on a stretched 480p display; use a separate profile for 720p.
  Existing `font_scale` and `hud_scale` keys remain aliases and must agree if combined.
  Values below `0.1` or finer than `0.001` now fail validation. Other menus with independent
  fixed dimensions retain their retail size.
  See [UI scaling](patches/ui-scale.md).
- `tes3x sym decompile` now starts Ghidra with its settings and temporary files under the project
  build folder, so restricted workspaces can launch the decompiler.
- Waiting and resting now work in multiplayer. Pressing Rest, Wait or Until Healed asks the other
  players to rest; each agrees by opening their own rest menu, and then everyone waits or rests
  for the same hours while the shared clock moves on. Before, the menu was refused while joined.
  See [the player guide](docs/multiplayer-client.md#limits).
- Spells made at the spell maker, potions brewed at the alchemy menu and items enchanted at the
  enchanting menu in multiplayer now reach the other players and survive a server restart; before,
  they existed only on the console that made them. The server keeps them in the world file and
  sends them to each player who joins, ahead of that player's own spells and items. See [the server guide](docs/multiplayer-server.md).
- Multiplayer servers can set `--level-up auto` (GUI: "Level up without resting") so players get
  the level-up screen as soon as they earn a level, without resting. The default keeps the retail
  rule. See [the server guide](docs/multiplayer-server.md).
- The multiplayer server list and address entry now have a dark backing with a thin gold border.
  Their former backing could show a bright placeholder texture on Xbox.
- Typing a new server's address opens the keyboard on the digits with `.`, `:` and `-`; the
  letter keys are one switch away.
- xemu screenshots now capture the run's own window when another xemu session is open.
  Capture failures are reported instead of silently leaving out the image.
- New `boot-clear` patch: a build that starts the engine without the retail launcher no longer
  flashes green lines before its first splash. See [the patch](patches/boot-clear.md).
- New `dash-button` patch: a hint line under the main and pause menus, with the pad's button icons,
  where White leaves for the Dash. The engine's own menu buttons are kept. Multiplayer puts Black
  on the same line: Join on the main menu, Save to Server on the pause menu, with White for
  Leave. See [the patch](patches/dash-button.md).
- Builds whose engine starts without the retail launcher (`--direct-engine` runs and the overlay
  layout) now include `boot-clear`, `menu-still` and `dash-button` together, and multiplayer
  requires all three. Disable them to keep the retail menu and videos.
- New `menu-still` patch: the main menu of such a build shows a still of the retail menu loop's
  parchment, emblem and Morrowind title instead of playing the logo movie and then a black
  screen. It needs `ffmpeg` on the `PATH` when building. See [the patch](patches/menu-still.md).

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
- The GUI's Content tab now gives the build list a resizable area, puts server controls and status
  at the top, and can start content hosting with a local multiplayer server. Both server states
  remain visible in the bottom status bar. See [content servers](docs/content.md).
- Large mods can go in a shared archive store (`store = true` on a mod, or
  `package.store_threshold_mb`): packed once into their own archive in `E:\TES3X\lib`, which
  every build using the mod shares, so the Xbox downloads it once. Deploy, xemu runs, content and
  multiplayer servers carry them; the Dash's **Settings > Shared archives** deletes unused ones.
  Builds that use the store need the updated Dash. See
  [shared archive store](docs/configuration.md#shared-archive-store).
- The Dash's tab bar now shows the previous, current and next tab, so it no longer reaches the
  title.
- The Docker setup now starts the multiplayer server and the content library as separate
  Compose profiles: `docker compose --profile multiplayer up -d`, `--profile content`, or both.
  The service formerly named `server` is now `multiplayer`. See
  [Docker](docs/multiplayer-server.md#run-the-server-in-docker).
- A multiplayer world can require an exact revision from a content library, on this PC or
  another, and authorize its players' Dashes to download it there. The game now reports the
  installed revision when it joins, and a console with a different revision or a local patch
  selection is refused. Update the game build, Dash and server together. See
  [builds from a content library](docs/multiplayer-server.md#builds-from-a-content-library).
- Fixed: a pipeline run failed with "content must be a table of content-server settings" once
  the GUI had saved the content server's multiplayer autostart setting.
- Content servers now use HTTPS, pinned through the library's signed catalog, and are private
  by default: a new Xbox waits until it is approved in the GUI or with `tes3x content approve`.
  Start the server with `--access public` to keep the earlier open behaviour. Each address is
  rate limited, `--client-rate` caps its bandwidth, and `tes3x content prune` deletes withdrawn
  revisions. Update the Dash and content server together. See [content servers](docs/content.md).
- New pipeline builds record their source profile and file hash in the build manifest and signed
  content catalog. Publishing one creates a new revision even if its game files match an older
  build; the game-content build ID stays the same. See [content servers](docs/content.md).
- Content and legacy multiplayer downloads now cap simultaneous HTTP connections at 32 and close
  idle connections after 30 seconds. Extra connection attempts must retry. See
  [content servers](docs/content.md).
- `tes3x content key` prints an existing content service's public signing key and fingerprint
  for configuring a remote source. See [content servers](docs/content.md).

- `tes3x game-tests` runs every game test against the retail engines named in the local
  config's new `[engines]` table, several at once, and prints what passed and failed. See
  [testing](docs/testing.md#every-game-test).
- `profile` targets can name a content locator, such as `profile=%chunk-read`, which finds the
  same function on NTSC and PAL engines. See the [profiler](patches/profile.md#using-it).
- The sampled PAL GOTY engine now resolves console and multiplayer bindings automatically;
  its virtual keyboard and server join work. Other retail engine revisions still need
  compatibility checks.
- Every patch combination now builds without a compiler. TES3X carries each payload source
  compiled once and links the selected ones for your retail XBE, filling in that engine's
  addresses, on the sampled NTSC and PAL GOTY engines. The portable folder no longer bundles
  LLVM; it is needed only to change payload sources, and `--build-payload` compiles every one.
  See [payload injection](patches/payload.md).
- A payload built by an earlier TES3X is refused by `tes3x patch`; build it again.
- xemu disc additions and direct-engine boot break staging hardlinks before replacing files,
  preserving the source game folder when runs share a deploy tree.
- Follow, escort, travel and wander orders from conversations and quest scripts are shared in
  multiplayer: a companion keeps following the player who hired them in every player's game, and
  after a server restart waits where it was until that player comes back to it. Update the server
  and install the build again. See
  [saving and character state](docs/multiplayer-client.md#saving-and-character-state).
- Quest scripts and the characters they move are shared in multiplayer: a script a conversation
  or quest starts is kept by the server with its progress and carries on in another player's
  game when you leave or after a restart, and a character moved by a script or a conversation
  is in the same place for every player, including players who join later. Update the server and
  install the build again. See
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
