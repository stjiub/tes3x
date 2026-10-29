# Record-load profiler

Patch key: `vtprof`

The function profiler times calls it can redirect, so it cannot split the time the engine spends
loading each record type: every record is loaded through one indirect call whose target depends on
the type. This patch times that call and totals it per record type.

See the [patch table](../docs/patches.md) for availability and selection.

## How it works

The patch (`hooks/tes3xvtprof.c`) replaces `BaseObject::loadFromFile` with the same steps around
two cycle-counter reads. It keeps the calls and cycles per vtable pointer, which identifies the
record type. Use it with the [function profiler](profile.md): each dump also logs one `vtprof.bin`
line per vtable to `E:\tes3xlog.txt` with the vtable address, the calls and the cycles in units of
1024. Name a vtable with `tes3x sym`.

The patch also times each form's whole load by its first subrecord tag, with the time spent in the
per-type load call apart, as `recprof.bin` and `recprof.specific` lines (tag, calls, cycles in units
of 1024). It does not combine with `lean-menu`, which redirects the same call.

The patch also times the engine's two passes over every loaded object after the records are read,
which call a per-type resolve step. Their lines carry the vtable pointer plus the pass number (1 or
2) in its low bits.
