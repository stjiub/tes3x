# Lowered first-person bow

Patch key: `bow-view`

In first person, a drawn bow and the arms holding it cover much of the screen. This patch lowers
them while an arrow or bolt is nocked, so more of the target stays visible.

See the [patch table](../docs/patches.md) for availability and selection.

## How it works

The hook (`hooks/tes3xbowview.c`) adjusts the viewmodel root after
`MobilePlayer::update1stPersonTransform`, only while a projectile is nocked. It does not change
camera height, field of view, projectile direction, or third-person animation.

## Configuration

`[Xbox] BowViewOffsetZ` in `Morrowind.ini` is a signed vertical offset in game units. The default
is `-12`; values are clamped to `-64` through `64`, and zero keeps the retail position. The setting
is read when a nocked projectile is first seen, because the game drive is not available at process
entry.
