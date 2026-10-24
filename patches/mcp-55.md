# Alchemy potion naming and stacking

Patch key: `mcp-55`

The alchemy menu resets a custom potion name after Create, including when brewing fails. This
patch keeps the entered name while Create refreshes the menu, so repeated attempts do not need
the name entered again.

Closing alchemy can also leave cached inventory icons out of sync with the player's items. The
patch rebuilds those icons from the player's inventory before updating the menu.

## How it works

The Create callback refreshes the menu after attempting a brew. The patch suppresses only the
name-field reset during that refresh. The alchemy close callback now uses the same inventory icon
rebuild sequence as the inventory menu.

See the [patch table](../docs/patches.md) for availability and selection.
