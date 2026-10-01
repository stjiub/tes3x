# Build-selected player preferences

The Xbox stores player options in a signed file that cannot be edited on a PC. This patch lets a
build profile choose some of those preferences, applied after the stored options load, so a
build starts with them already set.

See the [patch table](../docs/patches.md) for availability and selection.

## How it works

The hook (`hooks/tes3xprefs.c`) runs after the stored options load. Vertical look is represented by
the `Look Up` and `Look Down` action rows rather than a separate flag. The hook swaps those rows
only when they still contain the right-stick vertical directions; unrelated bindings and custom
non-stick assignments are left alone. The game's normal options writer can persist the resulting
binding table with its required Xbox signature.

## Configuration

A profile's `[preferences]` table selects the patch; the pipeline applies it whenever the table is
present.

```toml
[preferences]
invert_look = false
```

Omitting `[preferences]` leaves both stored preferences and retail defaults unchanged.
