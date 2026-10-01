# Save and load without forced sleeps

Patch key: `loop-sleeps`

The Xbox build pauses its own game thread while saving and loading. Loops that walk every object
in the game call `Sleep(1)` after each 16th, 64th or 128th object, and the PC build has no such
calls. The more objects a game holds, the longer it sleeps, so a large modded game freezes for
seconds on every save and load. This patch turns those calls into `Sleep(0)`, which
still lets any waiting thread of the same priority run, but returns at once when none is waiting.

See the [patch table](../docs/patches.md) for availability and selection.

## How it works

`Sleep` is found by its own code: `push 0; push [esp+8]; call SleepEx; ret 4`. Each loop has the
same shape: increment a counter, test its low bits, skip unless they are zero, `push 1`, call
`Sleep`. The patch changes the `push 1` to `push 0` at all 13 such calls, and refuses to apply if
it finds a different number. The calls are in:

- the teardown after a save is written (`0x00107910`, three loops), which also runs when a save is
  loaded;
- the load walks (`0x00112450`, six loops; `0x00113290` and `0x0010AE60`, one each);
- `WorldController` at `0x0008C970` (two loops).

Other `Sleep` calls, such as fixed waits and the ones in streaming and audio code, are left alone.

## Compatibility and limits

A thread of lower priority than the game thread no longer gets the CPU during these walks.
