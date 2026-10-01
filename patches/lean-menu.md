# Low-memory main menu

On the Xbox, starting a new game or loading a save relaunches the title, and the new process loads
every record again. The process that shows the main menu has already loaded them all, only to
throw them away. This patch has the menu process load just the game settings its text needs, so
the menu appears sooner and uses less memory.

See the [patch table](../docs/patches.md) for availability and selection.

## How it works

The patch decides once, when the first record loads, whether this process is the menu. It is when
there is no relaunch data and neither "No Reboot" flag is set; otherwise the process is the game
itself and loads normally.

In the menu process the hook (`hooks/tes3xleanmenu.c`) sits on the call that loads each record.
It reads the record's tag and skips everything but `TES3` and `GMST`, reporting success; the file
reader then seeks past the record by its size. Two further calls would fail without the skipped
records, so the patch replaces them:

- The menu is shown only if the `[PreLoad] Cell 0` lookup succeeds, and the result is discarded
  at once. The hook reports it found.
- New Game creates the player before it decides to relaunch. The hook skips that creation, since
  the relaunched process creates the player itself.

## Compatibility and limits

The patch relies on the relaunch. A build that sets `[Debug] No Reboot On New Game` or
`No Reboot On Load Game` in `Morrowind.ini` loads normally in the menu process.
