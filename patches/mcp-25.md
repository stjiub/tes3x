# Blind attack penalty

Patch key: `mcp-25`

Blind can raise the player's chance to hit instead of lowering it. This patch makes Blind apply
its intended attack penalty to the player.

## How it works

The Blind effect sends a signed magnitude to the player's attack modifier. The Xbox engine
negates that magnitude on the player path, reversing the effect. The patch removes that negation.
Other actors' Blind effects retain their existing path.

See the [patch table](../docs/patches.md) for availability and selection.
