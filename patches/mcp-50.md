# Stable enchantment sort

Patch key: `mcp-50`

The magic menu can change the order of enchanted items after an inventory action. This patch
sorts the list alphabetically each time the menu refreshes, so the same items stay in a
predictable order.

## How it works

The menu already has an alphabetical sort, but a refresh flag decides whether to run it. The
patch removes that branch and runs the existing sort on every refresh.

See the [patch table](../docs/patches.md) for availability and selection.
