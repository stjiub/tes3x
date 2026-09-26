# Managed mod library

TES3X profiles are resolved as ordered overlays. The source library is never modified: each later
mod wins conflicting virtual `Data Files` paths, and the resolved view is compiled into a BSA and
loose plugin set for the Xbox. This is the build-time equivalent of a PC mod manager's virtual
filesystem.

The GUI uses a managed `library.toml` at the library root. It gives packages stable ids, permits
multiple installed releases and describes optional installer components. See
[`examples/library.toml`](../examples/library.toml).

Create a conservative initial index from an existing one-folder-per-mod library:

```powershell
python tools/tes3x_library.py scan "D:/Morrowind Mods" --write
```

The scan records each top-level folder or standalone plugin as one `unknown` release. It does not
guess optional-folder meaning or versions; edit those choices once, then reuse them in profiles.

```toml
schema = 1

[[mod]]
id = "travel-mod"
name = "Travel Mod"

[[mod.release]]
version = "1.0"
folder = "Travel Mod 1.0"
default = true
roots = ["00 Core"]
dependencies = ["shared-assets"]

[[mod.release.component]]
id = "travel-music"
name = "Travel music"
roots = ["10 Travel Music"]
default = false
```

The corresponding portable profile selection is:

```toml
[[mods]]
id = "travel-mod"
version = "1.0"
components = ["travel-music"]
plugins = ["TravelMod.esp"]
order = 10
```

`roots` are ordered overlay layers relative to the release folder. Release roots apply first,
then selected components in profile order. A component may name a `group`; selecting two members
of one group is rejected. Its `conflicts` list may reject other component ids explicitly.
Dependencies name other installed mod ids. Profiles keep dependencies explicit; the GUI and
library batch tester insert their default releases before the mod that needs them.

Component roots nested beneath a release root are automatically hidden from that base layer. This
supports installer layouts whose normal files are at the package root beside `Optional` folders:
an optional subtree enters the virtual Data Files view only when its component is selected.

Set the library once in the machine-local config:

```toml
[paths]
mod_library = "D:/Morrowind Mods"
```

`profile.library` remains available and takes precedence. The GUI and CLI consume the same files;
the GUI is an editor, not a separate mod database.

## Profile manager

Install the optional GUI dependencies and open a profile:

```powershell
python -m pip install -r requirements-gui.txt
python tools/tes3x_gui.py profile.toml
```

The profile manager uses separate **Mods**, **Patches** and **Output** tabs. The patch tab reads the
real `patches.toml` catalog, groups patches by category, filters by text, category or channel, and
shows summary, origin and validation information. Presets, whole categories and explicit
enable/disable choices are written to the normal `[patches]` profile table; an expandable summary
shows what that profile applies.

The GUI also checks profiles, builds, runs smoke tests and performs size-verified Xbox deployments.
Its local settings dialog edits `tes3x.local.toml` while retaining comments and xemu-specific
fields. The status bar checks the configured FTP connection; **Pull Xbox logs** fetches
`E:/tes3x*` into a timestamped directory under `build/xbox-logs/`. Successful deployment output
can optionally be discarded only after transfer verification. Profile and local-file operations
are under **File**; build, test and Xbox operations are under **Actions**. Command output remains
visible below the tabs. Package import and richer conflict visualization can build on the same
library module without changing the profile format.

## Compatibility ledger

Compatibility is deliberately separate from the installed library and build profiles. Maintain a
`mods.toml` like [`examples/mods.toml`](../examples/mods.toml), then generate its readable view:

```powershell
python tools/tes3x_mods.py mods.toml --write docs/mod-compat.md
```

Its status is edited only by a person after observation. Automated results can be cited in
`validation`, but no test command edits or promotes the status.
