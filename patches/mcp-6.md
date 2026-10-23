# Restore fortified attributes

Patch key: `mcp-6`

When a temporary Fortify Attribute effect is active, Restore Attribute can stop at the unfortified
base value. An expiring Drain Attribute can leave the same loss. This patch lets both paths recover
the active fortified value.

See the [patch table](../docs/patches.md) for availability and selection.

## How it works

The magic controller counts active temporary Fortify Attribute magnitudes for the player. While
Restore or Drain updates an attribute, it includes that amount in the attribute's upper limit,
then restores the stored base value. Permanent spell abilities continue through their existing
path.
