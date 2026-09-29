# rotating-autosaves

Automatic saves rotate through several slots instead of overwriting one. The idea comes from
OpenMW's autosave settings.

The three automatic-save calls are redirected (`hooks/tes3xsaves.c`); quicksave and manual saves
are untouched. In `Morrowind.ini`, `[Xbox] RotatingAutosaves=0` restores vanilla behaviour without
repatching, and `AutosaveSlots` sets 1 to 9 slots, default 3. Slot 1 keeps the vanilla name; later
slots add their number. A one-byte counter in `T:\tes3x-autosave.dat` advances after each
successful save, so rotation survives the title relaunch that happens on load.
