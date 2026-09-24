# transition-autosaves

Saves immediately before player cell transitions that can lead to a loading screen: activated
doors, scripted and console teleports, intervention-style magic, and paid travel. Load-game
restoration and ordinary exterior-grid streaming are deliberately excluded.

The save uses the rotating-autosaves helper, so the two patches share slot names and rotation
state when both are enabled. Without rotating-autosaves, transition saves overwrite the retail
autosave slot. `[Xbox] TransitionAutosaves=0` disables these extra saves without repatching.

Status and selection policy are in the [patch table](../../docs/patches.md).
