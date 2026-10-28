# Indexed script lookup by ID

Patch key: `script-index`

The engine finds a script by ID by walking its whole list of scripts and comparing each one. It
does this for every script record it loads, every global script it starts and every script
reference it links, so the work grows with the square of the number of scripts. With the Tamriel
Rebuilt masters these walks took about 18 s of the console's load to the main menu. The
index brings that to about 0.1 s.

See the [patch table](../docs/patches.md) for availability and selection.

## How it works

The patch (`hooks/tes3xscriptindex.c`) keeps a hash index over the script list and answers the
lookup from it. A script's ID is set while its record loads, before the script joins the list, so
the index only has to follow the list. It adds scripts as they are appended and rebuilds when the
list changes in any other way, such as a later plugin replacing an earlier script. Each candidate
is compared with the script's current ID, and when several scripts match the patch returns the
first in the engine's list, as the engine's own walk does. The engine's walk also leaves the
list's cursor on the match, or empty when nothing matches; the patch does the same. If it cannot
get memory for the index, it falls back to the engine's walk.

## Configuration

`[Xbox] ScriptIndex=verify` in `Morrowind.ini` runs the engine's walk beside every indexed lookup,
returns the engine's answer, and logs `scriptindex.mismatch` with both scripts when they differ.
`scriptindex.checks` counts the lookups compared.
