# Delayed spell crash fix

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

## What remains

Ordinary cell changes run through the hook, but no test has yet constructed a stale NPC cast or
reproduced the delayed crash.
