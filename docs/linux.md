# Linux

TES3X is developed on Windows and is meant to run on Linux too. Nothing here has been tried on
Linux end to end yet: this page lists what is known to be missing so that gaps are fixed, not
rediscovered.

## What works

Tool lookups find `llvm-objcopy`, `llvm-objdump`, `7zz`, `clang++` and the Steam install on
Linux. The data folder is `~/.local/share/tes3x`, or `TES3X_DATA` when set; see
[Getting started](getting-started.md).

## Known gaps

- `tes3x layouts` needs MSVC and Windows SDK headers; another header source is needed.
- `tes3x xemu` (screenshot, crash events), `tes3x gui` (the `tasklist` xemu check) and
  `tes3x ghidra` (finding the Java process) call Windows-only tools.
- `rules.tes3merge` runs `TES3Merge.exe`: it needs Wine or an alternative.
- `tes3x package` and `launcher/tes3x.c` build a Windows folder (embedded Python, `7z.exe`,
  LLVM, `TES3X.exe`); Linux needs its own route.
- The GUI, the pipeline and an xemu run have not been run on Linux.

## For contributors

Use `pathlib`, and do not hard-code `.exe`, drive letters, `%LOCALAPPDATA%` or PowerShell-only
steps in tools and docs. When touching a Windows-specific spot, make it platform-agnostic or add
it to the list above.
