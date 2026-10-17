# Spell deselection fix

Patch key: `mcp-47`

When another actor unequips a magic item, the retail engine can clear the player's selected
spell. This patch keeps the player's selection, avoiding an unnecessary trip through the magic
menu during play.

## How it works

The actor unequip path clears that actor's equipped magic, then calls the player's magic-menu
deselection routine. The patch lets that menu call run only when the unequipping actor is the
player. Removing the player's own selected magic item still clears its selection.

See the [patch table](../docs/patches.md) for availability and selection.
