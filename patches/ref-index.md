# Indexed reference lookup at global script start

Patch key: `ref-index`

Global scripts resolve every object they name when they start. This patch builds one temporary
index of loaded references for that phase, instead of walking every cell again for each object.

See the [patch table](../docs/patches.md) for availability and selection.

## How it works

The patch wraps `WorldController::startGlobalScripts`. On the first reference lookup inside that
call, it walks the cell lists once and records the first live reference for each base object,
falling back to the first deleted reference just as the original search does. Lookups use the
index until global-script startup returns, then the table is freed. If its allocation fails, every
lookup uses the original cell walk.

Instance records are also indexed under the original object they represent. The table exists only
while global scripts start, when the reference lists cannot change underneath it.
