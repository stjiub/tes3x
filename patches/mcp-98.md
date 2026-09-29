# Animated container crash fix

Accessing an animated container can destroy its live animation while the engine is still using it.
The failure is easiest to provoke when a merchant checks containers for sellable items, or when one
container is the only instance in a cell.

The Xbox function at `0x000E7770` contains the same bad reference-count operations as the PC build.
It assigns the shared animation correctly, but also increments the first container reference at
`0x000E77EC` and later decrements the other at `0x000E7859`. If that decrement reaches zero, the
function tears down the animation before returning.

The patch removes the increment and skips the paired decrement and teardown, matching the MCP fix.
It changes three bytes and needs no injected code.

## What remains

The equivalent Xbox code and both edits are established statically. The merchant and single-instance
container crash cases still need control-versus-patched game tests with an animated-container mod.
