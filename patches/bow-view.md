# Bow view

This patch lowers the first-person bow and arms only while an arrow or bolt is nocked. It adjusts
the viewmodel root after `MobilePlayer::update1stPersonTransform`; it does not change camera height,
field of view, projectile direction, or third-person animation.

`[Xbox] BowViewOffsetZ` is a signed vertical offset in game units. The default is `-12`; values are
clamped to `-64` through `64`. Zero retains the retail position. The setting is read when a nocked
projectile is first seen because the game drive is not available at process entry.

The default offset is an initial tuning value and has not been compared on a 480p display.
