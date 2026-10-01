# Separate save title ID

An Xbox title keeps its saves in `E:\UDATA\<title ID>`, so every Morrowind build on a console shares
one save folder. This patch gives a build its own title ID, and with it a separate save folder, so
test builds cannot touch the saves of the game you play.

See the [patch table](../docs/patches.md) for availability and selection.

## How it works

The certificate's title ID (certificate offset `0x18C`) is replaced with the given 32-bit value.
The pipeline applies the same ID to `Default.xbe` and `morrowind.xbe`, or the launcher and the
engine would use different folders.

## Configuration

A profile names a save pool with `profile.save_pool`; builds with the same pool share saves. The
pool's ID is derived from its name, or set with `profile.save_pool_id`. Directly, the patch is
`--apply title-id=HEX`. See [configuration](../docs/configuration.md).

## Compatibility and limits

The engine faults if it has to create a new save folder itself, so the pipeline writes the folder's
title metadata and image, and a marker naming the pool, before a pooled build first runs. Derived
IDs start with `T3`, outside the range licensed titles use.
