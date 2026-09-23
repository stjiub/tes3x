# Patches

Generated from [`patches.toml`](../patches.toml) by `tools/tes3x_patches.py --write`.
Edit that file, not this one.

**Status**: `implemented` means the patch applies and passes structural checks;
`verified-xemu` means it was shown to work in the xemu emulator; `verified-hardware`
means it was shown to work on an original Xbox.

**Selected by**: `standard` and `development` are presets; a patch marked `standard` is
also in `development`. Anything else is enabled by name in a profile.

| patch | what it does | category | status | selected by |
|---|---|---|---|---|
| `payload=FILE.pe` | Inject a code section and run it from the entry point. | infrastructure | verified-xemu | command line |
| `boot-media` | Permit booting from any media and region, not just a retail DVD. | infrastructure | verified-xemu | every build |
| `drive-letters=LETTER` | Point every Data Files asset path at one drive. | infrastructure | verified-xemu | every build |
| `save-staging=LETTER` | Stage saves on one volume with UDATA so the commit renames instead of copying. | infrastructure | implemented | command line |
| `title=NAME` | Rename the image, so parallel installs are told apart in a dashboard. | infrastructure | implemented | command line |
| `multi-bsa` | Load every archive listed in tes3xarch.txt, not just Morrowind.bsa. | infrastructure | verified-xemu | delta-bsa packing |
| `script-ext` | Add script opcodes: widen the length bounds and hook Script::RunFunction. | compat | verified-xemu | by name |
| `mcp-1` | Stop an unresolvable reference being restamped as created at runtime. | core | implemented | by name |
| `mcp-97` | Advance the script parser correctly while initializing saved data. | core | verified-xemu | standard |
| `mcp-154` | Pad compiled script-data allocations to keep dword reads in bounds. | core | implemented | by name |
| `mcp-102` | Reactivate script-triggered objects after their script mod is removed. | correctness | verified-xemu | standard |
| `mcp-140` | Throttle loading-screen redraws to one every 50 milliseconds. | performance | implemented | by name |
| `rotating-autosaves` | Rotate automatic saves through INI-configurable slots. | qol | implemented | by name |
| `diagnostics` | Enable INI-controlled crash records, snapshots and a hang watchdog. | instrumentation | verified-xemu | development |
| `console` | Make the in-game console reachable, by replacing its input gate. | qol | verified-xemu | development |
| `profile=VA[,VA...]` | Time listed functions with RDTSC at every direct call site. | instrumentation | implemented | command line |
