# Unrestricted boot media

The retail XBE's certificate allows it to run only from a retail DVD, in the regions it was sold
in. This patch allows every media type and region, so the game runs from the hard disk of a
modded console. Every pipeline build applies it.

See the [patch table](../docs/patches.md) for availability and selection.

## How it works

The certificate's `dwAllowedMedia` field (certificate offset `0x220`) becomes `0xC00001FF`, every
media flag, and `dwGameRegion` (`0x224`) becomes `0x7`, every region. Nothing else changes. This
is the certificate half of the scene's standard hex edit.
