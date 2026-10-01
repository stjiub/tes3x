# Rotating autosaves

Patch key: `rotating-autosaves`

Retail overwrites one autosave slot every time, so a bad autosave replaces the only good one. With
this patch automatic saves rotate through several slots instead. The idea comes from OpenMW's
autosave settings.

See the [patch table](../docs/patches.md) for availability and selection.

## How it works

The three automatic-save calls are redirected (`hooks/tes3xsaves.c`); quicksave and manual saves
are untouched. Slot 1 keeps the retail name; later slots add their number. A one-byte counter in
`T:\tes3x-autosave.dat` advances after each successful save, so rotation survives the title
relaunch that happens on load.

## Configuration

In `Morrowind.ini`, `[Xbox] RotatingAutosaves=0` restores retail behaviour without repatching, and
`AutosaveSlots` sets 1 to 9 slots, default 3.
