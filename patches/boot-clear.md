# Clear video memory at boot

Patch key: `boot-clear`

When the game engine starts without the retail launcher, as every TES3X build does, its first
loading splash can flicker with green horizontal lines. This patch clears the screen to black
before the first frame, so the splash starts clean.

See the [patch table](../docs/patches.md) for availability and selection.

## How it works

The retail launcher leaves a finished picture in video memory for the engine to start from. Started
on its own, the engine presents its first frame before it clears the colour buffer, so it shows
whatever the video memory held. The hook (`hooks/tes3xbootclear.c`) runs once, right after the
engine creates its renderer. It clears both display buffers to black and presents each, then lets
the engine continue unchanged.
