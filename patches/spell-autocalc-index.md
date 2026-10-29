# Indexed NPC spell auto-calculation

Patch key: `spell-autocalc-index`

When an NPC's spells are auto-calculated, the engine visits every spell in the game and rejects
most of them at once: a spell the NPC cannot afford, or that needs a skill or attribute the NPC
lacks. The visit reads about two thousand spell objects spread across memory for each NPC, which is
about 5.6 s of the console's load to the main menu with the Tamriel Rebuilt masters (6,506 NPCs).
The patch hands the engine only the spells that pass those first tests.

See the [patch table](../docs/patches.md) for availability and selection.

## How it works

The patch (`hooks/tes3xautocalcindex.c`) replaces the two calls that step through the spell list.
It keeps, in list order, a small record for every spell that could be picked: its cost and the
skill or attribute behind each effect. For an NPC it passes over a spell when the NPC cannot cast it
for one of those reasons, which are the first tests the engine itself applies, and the engine runs
all its other tests as before, so the NPC gets the same spells. A walk for the player is not
filtered. The records are rebuilt when the spell list's head, tail or count changes; when it cannot
get memory for them, the engine walks the list itself.

## Configuration

`[Xbox] AutoCalcIndex=verify` in `Morrowind.ini` checks every record against its spell as the walk
passes it and logs `acalc.mismatch` when they differ; `acalc.checks` counts the records compared.
`AutoCalcIndex=off` leaves the engine's walk in place.
