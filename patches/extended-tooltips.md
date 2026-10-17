# Extended tooltips

Patch key: `extended-tooltips`

An item or container that the player would steal by activating it shows a red **Steal** line in
its in-world tooltip. The check follows the engine's ownership rules: an NPC's conditional global
can permit taking an item, and faction membership and rank can permit faction property.

Menu tooltips show the named owner or faction and any required faction rank. Weapons show speed
and reach; arrows and bolts show their projectile damage. Enchanted items show their cast type and,
when available, current and maximum charge. A filled soul gem names its soul.

## How it works

The patch adds lines to the Xbox tooltip without changing pickup or crime rules. See the
[patch table](../docs/patches.md) for availability and selection.
