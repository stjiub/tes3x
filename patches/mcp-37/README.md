# Delayed spell crash fix

See the [patch table](../../docs/patches.md) for policy and status.

Leaving a cell while an NPC is still in its casting animation can leave that cast in the magic
manager after its actor reference leaves the active cell. When the engine later cleans up the
reference, the stale cast can dereference it and crash.

The patch hooks the cell-change path at `0x000C1CF9`, after the old world state is torn down and
before the player is installed in the destination. It walks the magic manager's active-cast tree
and changes an in-progress cast from state 0 to state 7 when its actor is not the player. This is
the Xbox equivalent of MCP's cleanup and also repairs stale casts already present in a save.

The hook calls the Xbox tree iterator at `0x000BC7F0`; the PC iterator MCP uses has a different
nil-node representation and cannot be copied byte for byte. The Xbox game object also stores the
magic manager at `+0x6C`, rather than the PC build's `+0x70`.

## Validation

The retail signature, hook target and payload pass structural verification. `scenario.toml`
starts a real game, changes cells and queries the player afterward; it exercises the hook path
but does not construct a stale NPC cast or reproduce the delayed crash.
