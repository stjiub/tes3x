# transition-autosaves

Saves immediately before player cell transitions that can lead to a loading screen: activated
doors, scripted and console teleports, intervention-style magic, and paid travel. Load-game
restoration, ordinary exterior-grid streaming and character generation are deliberately excluded.
The character-generation scripts do not permit saving until the player leaves the Census and
Excise Office, so the patch follows the same gate as retail quicksave.

The save uses the rotating-autosaves helper, so the two patches share slot names and rotation
state when both are enabled. Without rotating-autosaves, transition saves overwrite the retail
autosave slot. `[Xbox] TransitionAutosaves=0` disables these extra saves without repatching.
