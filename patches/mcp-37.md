# Cell-change casting crash fix

Patch key: `mcp-37`

Leaving a cell while an NPC is still in its casting animation can leave that cast in the magic
manager after its actor reference leaves the active cell. When the engine later cleans up the
reference, the stale cast can dereference it and crash. This patch cancels such casts when the
cell changes.

See the [patch table](../docs/patches.md) for availability and selection.

## How it works

The patch hooks the cell-change path at `0x000C1CF9` (`hooks/tes3xmcp37.c`), after the old world
state is torn down and before the player is installed in the destination. It walks the magic
manager's active-cast tree and changes an in-progress cast from state 0 to state 7 when its actor
is not the player. This is the Xbox equivalent of MCP's cleanup and also repairs stale casts
already present in a save.

The hook calls the Xbox tree iterator at `0x000BC7F0`; the PC iterator MCP uses has a different
nil-node representation and cannot be copied byte for byte. The Xbox game object also stores the
magic manager at `+0x6C`, rather than the PC build's `+0x70`.

Each cell change that cancels casts logs `mcp37.cancelled N`.

## Compatibility and limits

A cast needs a few frames to register: one begun on the frame before the cell change is not in the
tree yet, and is not cancelled.
