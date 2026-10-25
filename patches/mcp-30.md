# Projectile aiming fix

Patch key: `mcp-30`

Projectiles can leave the bow off centre for races with unusual height when aiming up or down.
Third-person shots can also travel away from the intended aim. This patch adjusts the bow's
release animation and normalizes the player's projectile launch transform so shots follow the
intended direction more closely.

## How it works

The bow release animation uses one consistent angle for its four aiming transforms. When the
player releases a projectile, the patch normalizes the horizontal basis of its launch matrix
before the engine combines that matrix with the projectile transform.

See the [patch table](../docs/patches.md) for availability and selection.
