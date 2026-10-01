# Data Files drive redirect

The retail game reads `Data Files` through `Z:`, the title's cache partition. A game installed on
the hard disk keeps its data beside the XBE instead, on `D:`, the drive the title was launched
from. This patch points every `Data Files` path at one drive, so the engine reads the installed
files and mods can be added to them. Every pipeline build applies it, with `D` unless the profile
says otherwise.

See the [patch table](../docs/patches.md) for availability and selection.

## How it works

The engine names its asset locations as literal strings with a drive prefix: `Data Files\`,
`Data Files\%s`, `Data Files\Meshes`, `Data Files\Fonts`, `Data Files\Morrowind.bsa` and
`Data Files\morrowind.esm.map`. The patch rewrites the drive letter of every occurrence, and of
the one prefix the engine builds inline in code (`mov dword ptr [esp+0xC], "Z:\"`) rather than
from a string.

The scene's standard hex edit changes only three of these strings and the inline prefix, leaving
the archive and font paths on `Z:`. This patch changes all of them.

## Configuration

The letter comes from `package.drive_letter` in a profile, or the pipeline's `--drive`; directly,
`--apply drive-letters=D`.
