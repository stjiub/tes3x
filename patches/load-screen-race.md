# Loading screen stop race

Patch key: `load-screen-race`

Fixes a rare freeze when a loading screen ends within its first few frames, for example when you
leave a building into a cell that is already in memory. The game stops responding with the
loading screen's last frame on the display, and nothing is logged.

See the [patch table](../docs/patches.md) for availability and selection.

## How it works

The engine draws loading screens on a separate render thread. When the load finishes, the main
thread asks that thread to stop by setting a busy flag and waits for the thread to clear it. The
render thread's loop checks the flag and exits, clearing it on the way out. Before its loop, the
thread calls the manager's start function once. That function begins by waiting for the flag to
clear, and with the manager active it does nothing else.

If the stop request arrives between the thread starting and that call, the thread waits for a flag
that only its own loop can clear, and the main thread waits for the thread. Both sleep for good.

Two hooks in `hooks/tes3xloadscreen.c` fix this. One notes which thread is the render thread when
it starts. The other replaces the thread's call of the start function and skips it when the
manager is active, so the thread reaches its loop, sees the stop request, and clears the flag. The
main thread's calls are untouched. When a call is skipped, the log records
`loadscreen.wait_skipped`.
