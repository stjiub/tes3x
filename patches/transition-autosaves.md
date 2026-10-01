# Transition autosaves

This patch saves immediately before player cell transitions that can lead to a loading screen:
activated doors, scripted and console teleports, intervention-style magic, and paid travel. A
crash or a bad outcome on the other side then costs little progress.

See the [patch table](../docs/patches.md) for availability and selection.

## How it works

The transition calls are redirected (`hooks/tes3xsaves.c`) to save first. Load-game restoration,
ordinary exterior-grid streaming and character generation are deliberately excluded. The
character-generation scripts do not permit saving until the player leaves the Census and Excise
Office, so the patch follows the same gate as retail quicksave.

## Configuration

`[Xbox] TransitionAutosaves=0` in `Morrowind.ini` disables these extra saves without repatching.

## Compatibility and limits

The save uses the [rotating autosaves](rotating-autosaves.md) helper, so the two patches share slot
names and rotation state when both are enabled. Without rotating autosaves, transition saves
overwrite the retail autosave slot.
