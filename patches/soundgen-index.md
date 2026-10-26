# Creature list for animation sound keys

Patch key: `soundgen-index`

An animation's sound keys (`SoundGen` footsteps, swim strokes, moans, roars, screams and landings)
are tied to the creature that uses the animation. The game finds that creature by walking every
loaded object, once per key, so a large load order pays for each footstep key in every animation
it loads, at startup and whenever a creature's animation is first loaded. This patch walks a list
of the loaded creatures instead.

See the [patch table](../docs/patches.md) for availability and selection.

## How it works

The patch replaces the walk with one over the creatures in the order the game would meet them,
so it picks the same creature. The list is built on first use and rebuilt whenever an object is
added to or removed from the game's object list. Names are read from each creature at every
lookup, so a creature whose record a later plugin changed is still matched by its current name.
If the list cannot be allocated, the original walk is used.
