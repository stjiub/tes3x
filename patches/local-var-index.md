# Indexed script variable lookup in dialogue

Patch key: `local-var-index`

A response in a dialogue topic can be limited by a script variable (`Local` and `NotLocal`
filters). While a response loads, the engine looks for a script that declares the variable by
asking every script in turn until one does. With the Tamriel Rebuilt masters that is 43.7 million
checks, about 30 s of the console's load to the main menu. The index answers each filter at
once.

See the [patch table](../docs/patches.md) for availability and selection.

## How it works

The patch (`hooks/tes3xlocalvarindex.c`) replaces the loader's walk. Only whether some script
declares the name matters, so it keeps a hash index of every declared variable name of every
script and compares each candidate with the current name, ignoring case as the engine does. It
adds scripts as they are appended and rebuilds when the script list changes in any other way, such
as a later plugin replacing a script. When it cannot get memory for the index it falls back to the
engine's walk. The walk's first step leaves the list's cursor on the first script; the patch does
the same.

## Configuration

`[Xbox] LocalVarIndex=verify` in `Morrowind.ini` runs the engine's walk beside every indexed lookup,
returns the engine's answer, and logs `localvar.mismatch` when they differ. `localvar.checks` counts
the lookups compared.
