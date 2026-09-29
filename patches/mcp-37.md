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

Each cell change that cancels casts logs `mcp37.cancelled N`. A cast needs a few frames to
register: one begun on the frame before the cell change is not in the tree yet.

## What remains

A cast begun 20 frames before a cell change is cancelled, but the delayed crash itself has not been
reproduced.
