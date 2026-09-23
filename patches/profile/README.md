# profile

RDTSC timing of up to 16 engine functions, chosen when the XBE is patched:
`--apply profile=VA[,VA...]`, or the pipeline's `--profile-target`.

Each target's direct call sites are redirected to a timing stub, which swaps the caller's return
address so it works for any calling convention. Counters are written raw to `E:\tes3xprof.bin` on
a console command, at a frame interval, or at bounded loader checkpoints. Each block also records
free memory and the active calls, with caller, `this` and the first two stack arguments.

Never ship it in a play build: the stubs stay installed whether or not anything reads them. Only
timings from an original Xbox mean anything; xemu runs only prove it does not crash.
