# Changelog

No official releases yet. The first public release is in preparation.

## Unreleased

- The sampled PAL GOTY engine now resolves console and multiplayer bindings automatically;
  its virtual keyboard and server join work. Other retail engine revisions still need
  compatibility checks.
- `tes3x payload --portable-bindings` can build a diagnostics, console and multiplayer payload
  once for the sampled NTSC and PAL GOTY engines; `tes3x patch` fills the engine addresses,
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
