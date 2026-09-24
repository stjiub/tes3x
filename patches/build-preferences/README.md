# build-preferences

A build profile can apply selected player preferences after the Xbox's signed stored options
load. Omitting `[preferences]` leaves both stored preferences and retail defaults unchanged.

```toml
[preferences]
invert_look = false
```

Vertical look is represented by the `Look Up` and `Look Down` action rows rather than a separate
flag. The hook swaps those rows only when they still contain the right-stick vertical directions;
unrelated bindings and custom non-stick assignments are left alone. The game's normal options
writer can persist the resulting binding table with its required Xbox signature.
