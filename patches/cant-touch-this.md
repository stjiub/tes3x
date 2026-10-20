# Can't Touch This dodges

Patch key: `cant-touch-this`

When a melee attack misses a calm NPC, the NPC now plays one of the eleven dodge animations from the Can't Touch This mod, so a miss reads as a dodge instead of a target standing still. The player is never animated, and neither are creatures, paralyzed, staggered, knocked down or dead actors, or actors already playing a dodge.

See the [patch table](../docs/patches.md) for availability and selection.

## How it works

The mod's two animation files are merged into `Meshes/xbase_anim.kf` during the build. The engine only knows 150 animation group names and rejects the mod's new ones, so the patch plays each dodge through the stock `Idle9` slot. It gives the target a private copy of that slot, points it at the dodge's time range in the merged file, and puts the stock slot back when the animation ends, the actor is removed, or another animation asks for it.

The hook sits on the engine's attack roll. A miss against a qualifying NPC picks one of the eleven clips at random. The build reads each clip's time range from the merged file and links it into the patch.

## Using it

Enable the Can't Touch This mod in the profile; the build selects this patch and merges `xmissdodge.kf` and `xArmedDodgeV2.kf` into the base animation file. The mod's Lua scripts are not used. The animation merge needs the optional KF reader (`tes3x[kf]`).

## Compatibility and limits

- Only melee misses animate. Arrow and thrown-weapon misses do not.
- The dodge plays at the actor's scripted priority across all three body layers, as `mcp-132` does, which the patch requires. Which bones a given clip moves is up to the clip.
- Only one of the mod's clips can play on an actor at a time, and about sixteen actors can dodge at once.
