# Enable and Disable without local variables

Patch key: `mwse-enable-disable-vars`

`Enable` and `Disable` record the object's new state in the running script's local variables. A
script that declares none has no such record, and the game writes through a null pointer and
crashes. The patch skips that write when there are no local variables, so the object is still
enabled or disabled.

## How it works

The two flag writes are replaced by small functions that check for the missing record first. See
the [patch table](../docs/patches.md) for availability and selection.
