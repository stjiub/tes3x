# Changelog

No official releases yet. The first public release is in preparation.

## Unreleased

- Multiplayer documentation has separate [player](docs/multiplayer-client.md) and
  [server admin](docs/multiplayer-server.md) guides.

- Patch promotion accepts maintainer-reviewed play and manual checks; scripted tests are optional.
  `validate check --gate` reports missing scripted evidence as warnings. The patch table shows
  availability without a game-test column. See [patch policy](docs/patch-policy.md).

- `tes3x init` creates the example config and profile from bundled files, keeping existing files.
- `build` and `library convert` honor `paths.mod_library` and accept `--config`.
- Texture/archive caches, manager staging, dashboard backups and xemu runs default to the data
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
