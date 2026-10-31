# Extended tooltips

Patch key: `extended-tooltips`

An object that the player would be stealing from by activating it (an item, container, door, bed
or other activator) shows an **Owned** line in its in-world tooltip, in the normal text colour.
The check follows
the engine's ownership rules: an NPC's conditional global can permit using an object, and faction
membership and rank can permit faction property.

Menu tooltips show the named owner or faction and any required faction rank. Weapons show speed
and reach; arrows and bolts show their projectile damage. Enchanted items show their cast type and,
when available, current and maximum charge. A filled soul gem names its soul.

## How it works

The patch adds lines to the Xbox tooltip without changing pickup or crime rules. See the
[patch table](../docs/patches.md) for availability and selection.
