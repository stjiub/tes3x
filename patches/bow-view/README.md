# Bow view

See the [patch table](../../docs/patches.md) for policy and status.

This patch lowers the first-person bow and arms only while an arrow or bolt is nocked. It adjusts
the viewmodel root after `MobilePlayer::update1stPersonTransform`; it does not change camera height,
field of view, projectile direction, or third-person animation.

`[Xbox] BowViewOffsetZ` is a signed vertical offset in game units. The default is `-12`; values are
clamped to `-64` through `64`. Zero retains the retail position. The setting is read when a nocked
projectile is first seen because the game drive is not available at process entry.

The patch is opt in. Its default is an initial tuning value pending comparison on a 480p display.
