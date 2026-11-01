# Quick loot

Patch key: `xbox-quick-loot`

Looking at a container lists what is inside it in the world tooltip: up to six stacks as
"3 x Bread", then "+N more", or "Empty". D-pad up and down move a `>` marker through the list and
A takes the marked stack without opening the contents menu. Holding Black or White while pressing A
opens the normal menu instead. The patch requires `console` and `extended-tooltips`.

The D-pad is claimed only while a container is targeted, you are not in combat and no weapon or
spell is readied. Movement and attack inputs are never touched. Corpses and creatures keep the
normal menu.

## How it works

Taking an item runs through the engine's own contents menu code, so locks, traps, ownership,
theft and the `OnPCAdd` script event behave as they do when the menu is opened by hand. The menu
is built, the item moves with the engine's transfer routine, and the menu closes again without
leaving the game in menu mode. A locked container never reaches the menu, so nothing is taken from
it. See the [patch table](../docs/patches.md) for availability and selection.

Containers need the inventory menu enabled in the save, as for the normal contents menu.
