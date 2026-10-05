# Candidate fixes

Generated from [`candidates.toml`](../candidates.toml) by `tools/tes3x_patches.py --write`;
edit that file, not this one.

Fixes from other projects that we've looked at, and why each isn't implemented, yet or at
all. Every Morrowind Code Patch fix is here. Being listed doesn't mean the Xbox has the bug.
Implemented fixes are in [patches.md](patches.md).

**Priority** is a rough guess at how much a fix matters on the Xbox: `high` for crashes and
lost saves players are likely to hit, `medium` for bugs seen in normal play or that mods rely
on, `low` for minor ones, and `none` for fixes we won't port.

| status | fixes |
|---|---|
| candidate | 190 |
| deferred | 13 |
| not-applicable | 15 |
| rejected | 2 |

## candidate

Worth porting, but not yet shown to affect the Xbox build.

| fix | from | priority | category | notes |
|---|---|---|---|---|
| `mcp-127` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #127 | high | compat | **Map-texture conflict fix.** The world map breaks if _land_default.dds is replaced at a different size. TES3X's texture budget resizes textures, so a build can cause this; check the pipeline leaves that texture alone before porting. |
| `mcp-173` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #173 | high | core (intended default) | **Persist `RaiseRank`/`LowerRank`.** Missing dirty mark can break faction quests after reload. |
| `mcp-178` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #178 | high | core (intended default) | **Load-warning input crash.** Xbox has load warnings and a single controller path, so this is directly testable. |
| `mcp-6` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #6 | medium | correctness | **Restore/drain attributes fix.** Restore and expiring Drain stop at the base value instead of the fortified one; affects ordinary play. |
| `mcp-25` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #25 | medium | correctness | **Blind fix.** Blind gives the player an attack bonus instead of a penalty: an inverted effect. |
| `mcp-30` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #30 | medium | correctness | **Projectile aiming fix.** Projectiles fire off-centre for races with height other than 1.0, and third-person aiming is poor. |
| `mcp-46a` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #46 | medium | correctness (recommended) | **`Drop` places at the acting reference, not the player.** The engine treats every drop as if it came from the player inventory view, so scripted drops land at the wrong actor. Split from the rest of id 46 under rule 3. |
| `mcp-47` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #47 | medium | correctness | **Spell deselection bug fix.** The selected spell is dropped when other actors unequip or use up magic items; tedious to reselect with a controller. |
| `mcp-48` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #48 | medium | correctness | **Level-up stats bug fix.** Level-up offers a x1 multiplier when the correct one would take a stat to 100. |
| `mcp-55` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #55 | medium | correctness | **Alchemy naming/stacking fix.** Alchemy asks for a name that is already present after long play, and resets custom names. The prompt on Xbox is the on-screen keyboard. |
| `mcp-58` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #58 | medium | correctness (undecided) | **Save-count warning.** MCP warns before PC memory corruption at roughly 300 save files. Xbox uses `XCreateSaveGame` and different enumeration, so first determine whether any equivalent limit exists; do not port the PC threshold by assumption. |
| `mcp-95` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #95 | medium | compat | **Voiceover script functions fix.** StopSound mutes every actor voice and SayDone fires early. Dialogue and quest mods depend on both. |
| `mcp-118` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #118 | medium | core (intended default) | **Items dropped while levitating or falling are lost.** Dropping in third person while airborne places the object near `{0, 0, 0}`, which is normally unreachable. Data loss, not a preference. Confirm the Xbox third-person and levitation paths share the defect. |
| `mcp-138` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #138 | medium | correctness (recommended) | **Reliable `CellChanged` for scripted teleports.** Script-contract fix used by mods; preserve vanilla transition semantics outside the missed path. |
| `mcp-141` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #141 | medium | core (intended default) | **Script expression parser / `Random` fix.** `Random` on multiples of 256 can crash and corrupt its argument. Large, but directly relevant to the extended script surface. |
| `mcp-149` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #149 | medium | core (intended default) | **Dispose corpse before death processing completes.** An actor is disposable before its final processing runs, so `OnDeath`/`OnMurder` can never fire and some spell effects never expire. Unrecoverable in an existing save, and corpse looting/disposal is part of the console avoidance ritual, so Xbox exposure is high. Reproduce by disposing during the death animation. |
| `mcp-160` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #160 | medium | correctness | **RemoveItem weight fix.** RemoveItem reduces encumbrance by the requested quantity even when fewer items exist; mods leave players permanently misweighted. |
| `mcp-177` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #177 | medium | compat | **GetEffect/RemoveEffects fix.** GetEffect and RemoveEffects sometimes return 0 or do nothing; scripted mods rely on them. |
| `mcp-256` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #256 | medium | compat | **Map expansion (for Tamriel Rebuilt).** Expands the world map for Tamriel Rebuilt. Needed for TR, which itself needs 128 MB today. |
| `mcp-267` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #267 | medium | qol | **UI display quality fix.** Fixes half-pixel UI misalignment that blurs text and icons. Worth checking on TV output, where blur is most visible. |
| `mwse-clone-light-null` | [MWSE](https://mwse.github.io/MWSE/references/general/patches/) | medium | core (profile-dependent priority) | **Handle missing light data while cloning.** Validate against malformed or legal-but-unusual meshes. |
| `mwse-clone-sortadjust` | [MWSE](https://mwse.github.io/MWSE/references/general/patches/) | medium | core (profile-dependent priority) | **Correct `NiSortAdjustNode` cloning.** Same rule: a mod-library reproducer raises priority. |
| `mwse-clone-stringextra` | [MWSE](https://mwse.github.io/MWSE/references/general/patches/) | medium | core (profile-dependent priority) | **Correct `NiStringExtraData` cloning.** Mesh-driven lifetime bug; prioritize if a current asset reproduces it. |
| `mwse-dialogue-filter` | [MWSE](https://mwse.github.io/MWSE/references/general/patches/) | medium | performance (benchmark first) | **Replace dialogue filtering/search with a more efficient implementation.** High potential with PfP's dialogue-heavy master. Measure dialogue-open latency and memory with/without PfP, then regression-test filtering. |
| `mwse-disposition-filter` | [MWSE](https://mwse.github.io/MWSE/references/general/patches/) | medium | performance (benchmark first) | **Optimize disposition/dialogue filtering.** Test result identity, not just speed, against a dialogue-rich load order. |
| `mwse-enable-disable-vars` | [MWSE](https://mwse.github.io/MWSE/references/general/patches/) | medium | core (intended default) | **Avoid `Enable`/`Disable` crash when script variables are not initialized.** High script/mod relevance. Test local, global and absent script data. |
| `mwse-getdeadcount` | [MWSE](https://mwse.github.io/MWSE/references/general/patches/) | medium | performance (benchmark first) | **Avoid repeated expensive work in `GetDeadCount`.** Profile scripts which call it frequently and preserve dynamic death counts. |
| `mwse-global-access` | [MWSE](https://mwse.github.io/MWSE/references/general/patches/) | medium | performance (benchmark first) | **Speed script global-variable lookup.** Existing profiling found only about 22 script commands/frame in the baseline; require evidence this lookup matters in a real modded profile. |
| `mwse-ini-cache` | [MWSE](https://mwse.github.io/MWSE/references/general/patches/) | medium | performance (benchmark first) | **Cache repeated `DontThreadLoad`/INI reads.** First instrument call frequency. Preserve runtime configuration changes if vanilla observes them. |
| `mwse-journal-update` | [MWSE](https://mwse.github.io/MWSE/references/general/patches/) | medium | performance (benchmark first) | **Optimize journal update/search work.** Measure large-journal open/update time and verify ordering/quest indices. |
| `mwse-map-border-marker` | [MWSE](https://mwse.github.io/MWSE/references/general/patches/) | medium | core (intended default) | **Avoid crash while updating map markers at the drawable map border.** Relevant to map expansion and large landmasses if the Xbox map bounds match. |
| `mwse-paperdoll` | [MWSE](https://mwse.github.io/MWSE/references/general/patches/) | medium | core (intended default if Xbox path exists) | **Avoid paper-doll equip/unequip crash.** Xbox inventory rendering may not use the PC paper-doll path; prove applicability first. |
| `mwse-pathgrid-zero` | [MWSE](https://mwse.github.io/MWSE/references/general/patches/) | medium | core (intended default) | **Avoid NPC flee selecting a random node from a zero-node pathgrid.** Clean bounds case and likely inexpensive port once the AI path is located. |
| `mwse-showmap-fillmap` | [MWSE](https://mwse.github.io/MWSE/references/general/patches/) | medium | performance (benchmark first) | **Optimize `ShowMap`/`FillMap`.** Compare command time and map correctness across repeated calls. |
| `mwse-uncloned-removeitem` | [MWSE](https://mwse.github.io/MWSE/references/general/patches/) | medium | core (intended default) | **Avoid actor/item-removal crash on uncloned actors.** Establish exact actor lifetime and inventory precondition. |
| `mwse-vfx-load-bloat` | [MWSE](https://mwse.github.io/MWSE/references/general/patches/) | medium | core (intended default) | **Reject or repair invalid loaded VFX state which causes load errors and save growth.** Especially relevant to long-lived Xbox saves; measure record growth as well as crash behaviour. |
| `mwse-vfx-save-empty` | [MWSE](https://mwse.github.io/MWSE/references/general/patches/) | medium | core (intended default) | **Avoid crash while saving an active visual effect when none of its particles can be serialized.** Build a small spell/VFX save reproducer before locating the serializer. |
| `omw-live-inventory` | [OpenMW-4642](https://gitlab.com/OpenMW/openmw/-/merge_requests/4642) | medium | correctness (recommended) | **Refresh an open container/inventory after script `AddItem`/`RemoveItem`.** The reported vanilla defect leaves the menu stale and can permit duplication. Reproduce with the Xbox menu before choosing a hook; promote to `core` if data loss/duplication is confirmed. |
| `mcp-2a` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #2 | low | correctness (recommended) | **Fix inverted Mercantile contribution to price.** Isolate the formula error. |
| `mcp-2b` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #2 | low | balance (opt in) | **Reduce all price modifiers by 50% and alter zero-value handling.** Rebalance, not required to correct the inversion. Do not couple it to 2a. |
| `mcp-4` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #4 | low | correctness | **Calendar fix.** Month table and names disagree, so the calendar is visibly wrong. Cosmetic. |
| `mcp-5` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #5 | low | balance (opt in) | **Stop merchants equipping sold items.** Changes visible merchant behaviour. OpenMW exposes comparable behaviour as a disabled-by-default option. |
| `mcp-7` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #7 | low | correctness | **StreamMusic / master volume fix.** StreamMusic forcing full volume applies to scripts on Xbox; the master-volume half concerns PC settings storage. |
| `mcp-8` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #8 | low | balance (opt in) | **Calm spell behaviour.** Arguable intended behaviour, but OpenMW keeps classic behaviour by default. |
| `mcp-9` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #9 | low | correctness | **Vampire stats fix.** Constant-effect items worn while becoming a vampire over-raise stats. Rare. |
| `mcp-10` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #10 | low | balance (opt in) | **Reflected spell behaviour.** MCP's rationale includes making reflection less safe; that is balance as well as correctness. OpenMW also preserves classic behaviour by default. |
| `mcp-12` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #12 | low | correctness | **Enchant glow in fog fix.** Fog is not disabled when drawing enchant glow. The Xbox renderer differs, so confirm the white glow happens on Xbox first. |
| `mcp-13` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #13 | low | qol | **Show NPC health bar on healing.** Show the target's health bar when healing it. |
| `mcp-14` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #14 | low | balance | **Enchanting increases item value.** Balance change, opt in only; MCP ships it as an option rather than a defect fix. |
| `mcp-15` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #15 | low | balance | **Allow stealing from KOed NPCs.** Balance change, opt in only; MCP ships it as an option rather than a defect fix. |
| `mcp-16` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #16 | low | balance | **Spellmaking max. magnitude increase.** Balance change, opt in only; MCP ships it as an option rather than a defect fix. |
| `mcp-17` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #17 | low | qol | **Spellmaking max. duration reduced.** Shorter spellmaker duration cap, easier to set precisely; may suit the controller slider. |
| `mcp-18` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #18 | low | balance | **Exhaust NPCs with Damage Fatigue.** Balance change, opt in only; MCP ships it as an option rather than a defect fix. |
| `mcp-19` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #19 | low | correctness | **Disposition fix.** Disposition lost when a Personality effect expires above 100. |
| `mcp-20` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #20 | low | correctness | **Spell magnitude fix.** Variable-magnitude spells rarely reach their maximum. |
| `mcp-21` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #21 | low | correctness | **Dispel fix.** Dispel silently stacks until it always succeeds. |
| `mcp-22` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #22 | low | correctness | **Creature armor damage fix.** Creatures without weapons never damage armor. |
| `mcp-24a` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #24 | low | correctness (recommended if reproduced) | **Prevent pathological NPC potion-use frequency.** Separate a bad scheduling/throttle path from new AI. |
| `mcp-24b` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #24 | low | balance (opt in) | **Add magicka-potion decision-making to NPC AI.** New combat AI behaviour. |
| `mcp-26` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #26 | low | compat | **Gloss map fix.** Gloss maps for mods. The Xbox renderer path differs; establish that it shares the limitation first. |
| `mcp-29` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #29 | low | correctness | **Particle effects fix.** Particle emitters detach from scaled weapons and projectiles. |
| `mcp-32` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #32 | low | correctness | **Waterwalk fix.** Waterwalk cast while swimming applies stored fall damage. |
| `mcp-33` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #33 | low | balance (opt in) | **Training price/stats.** Formula looks incorrect, but trainer base-skill behaviour is configurable in OpenMW and affects progression/economy. |
| `mcp-34a` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #34 | low | correctness (recommended) | **Make displayed and charged travel prices agree.** A direct UI/transaction mismatch. |
| `mcp-34b` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #34 | low | balance (opt in) | **Charge separately for companions.** Economy choice; keep separate from the mismatch fix. |
| `mcp-36` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #36 | low | compat | **Createmaps/fillmap fix.** createmaps is a PC map-generation command; only the fillmap half, listing all mods, is useful now that the console is reachable. |
| `mcp-38` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #38 | low | correctness | **Loud interface/gameplay sounds fix.** Mostly a PC DirectSound duplicate-sound quirk; the armor-hit volume overflow may exist on Xbox. Reproduce before porting. |
| `mcp-40` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #40 | low | balance | **Strength-based hand to hand damage.** Balance change, opt in only; MCP ships it as an option rather than a defect fix. |
| `mcp-41` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #41 | low | balance | **Spellmaker/enchant multiple effects.** Balance change, opt in only; MCP ships it as an option rather than a defect fix. |
| `mcp-42` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #42 | low | balance | **Spellmaker area effect cost.** Balance change, opt in only; MCP ships it as an option rather than a defect fix. |
| `mcp-43` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #43 | low | balance | **Arrow enchanting.** Balance change, opt in only; MCP ships it as an option rather than a defect fix. |
| `mcp-44` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #44 | low | balance | **Fortify maximum health.** Balance change, opt in only; MCP ships it as an option rather than a defect fix. |
| `mcp-46b` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #46 | low | correctness (recommended) | **`PlaceAtPC` distance in third person, `PlaceAtMe` scale inheritance.** A line-of-sight test misplaces third-person `PlaceAtPC` at the player's feet; `PlaceAtMe` inherits a target's rotation but not its scale. Two separate defects sharing id 46's record set. |
| `mcp-49` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #49 | low | correctness | **Bound items expiry fix.** Expiring bound weapons force a weapon draw; bound armor does not restore gloves, bracers and shoes. |
| `mcp-50` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #50 | low | qol | **Stable enchantment sort.** Sort enchantments alphabetically every time. |
| `mcp-51` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #51 | low | correctness | **Fog of war fix.** Local-map fog of war can stop updating after a cell crossing because of mismatched rounding. |
| `mcp-52` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #52 | low | correctness | **Intimidate fix.** A successful intimidate can fail to raise disposition. |
| `mcp-53` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #53 | low | correctness | **Magicka display accuracy.** Stat displays round up and overstate current magicka. |
| `mcp-56` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #56 | low | correctness | **Spellmaker/enchant edit effect fix.** Editing a spell effect resets its range to Touch. |
| `mcp-57` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #57 | low | compat | **Allow scroll enchant price modifier.** Script or modding support; worth porting only when a mod used on Xbox needs it. |
| `mcp-64` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #64 | low | balance | **Multiple attribute fortify potions.** Allows potions with several fortify or drain attribute effects; changes alchemy results. |
| `mcp-65` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #65 | low | balance | **Soulgem value rebalance.** Balance change, opt in only; MCP ships it as an option rather than a defect fix. |
| `mcp-66` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #66 | low | balance | **On-use ring extra slot.** Balance change, opt in only; MCP ships it as an option rather than a defect fix. |
| `mcp-67` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #67 | low | balance | **NPC AI casts zero cost powers.** Balance change, opt in only; MCP ships it as an option rather than a defect fix. |
| `mcp-68` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #68 | low | balance | **Permanent barter disposition changes.** Balance change, opt in only; MCP ships it as an option rather than a defect fix. |
| `mcp-69` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #69 | low | balance | **Enchanted item cooldown.** Balance change, opt in only; MCP ships it as an option rather than a defect fix. |
| `mcp-70` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #70 | low | balance | **Hidden traps.** Balance change, opt in only; MCP ships it as an option rather than a defect fix. |
| `mcp-71` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #71 | low | balance | **Swift casting.** Balance change, opt in only; MCP ships it as an option rather than a defect fix. |
| `mcp-72` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #72 | low | correctness | **Blight storm disease fix.** Blight storms add diseases that never take effect. |
| `mcp-73` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #73 | low | balance | **Racial variation in speed fix.** Balance change, opt in only; MCP ships it as an option rather than a defect fix. |
| `mcp-74` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #74 | low | balance | **Game formula restoration.** Restores formulas that ignore their game settings; changes spell fatigue and other mechanics. |
| `mcp-75` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #75 | low | balance | **Creature armor rating fix.** Gives creatures armor rating damage reduction. |
| `mcp-76` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #76 | low | compat | **Disable weapon transition on unequip.** Script or modding support; worth porting only when a mod used on Xbox needs it. |
| `mcp-77` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #77 | low | balance | **Pickpocket overhaul.** Balance change, opt in only; MCP ships it as an option rather than a defect fix. |
| `mcp-79` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #79 | low | correctness | **Slowfall on companions fix.** Companions under slowfall can take fall damage across some cell changes. |
| `mcp-80` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #80 | low | balance (opt in) | **Drain Intelligence magicka exploit.** Exploit and balance change, not stability. |
| `mcp-81` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #81 | low | correctness | **Mouseover menu display fixes.** Tooltip errors: blank next-rank requirements, spells under the wrong heading. |
| `mcp-83` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #83 | low | compat | **Detect water level fix.** Scripts detect the player as underwater in cells without water. |
| `mcp-84` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #84 | low | compat | **Service refusal filtering.** Script or modding support; worth porting only when a mod used on Xbox needs it. |
| `mcp-85` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #85 | low | correctness | **Armor indicator fix.** Inventory armor rating does not update for shield spells. |
| `mcp-86` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #86 | low | balance | **Detect life spell variant.** Balance change, opt in only; MCP ships it as an option rather than a defect fix. |
| `mcp-87` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #87 | low | compat | **Scripted music uninterruptible.** Script or modding support; worth porting only when a mod used on Xbox needs it. |
| `mcp-88` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #88 | low | compat | **Argonian clothing choice.** Argonian females prefer male clothing models; convenience for clothing mods. |
| `mcp-89` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #89 | low | qol | **Vanity camera lock.** Vanity camera stays active with free mouse control. The Xbox camera controls differ; design a controller version if wanted. |
| `mcp-90` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #90 | low | balance | **Alchemy potion weight rebalance.** Balance change, opt in only; MCP ships it as an option rather than a defect fix. |
| `mcp-91` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #91 | low | balance | **Item recharging rebalance.** Balance change, opt in only; MCP ships it as an option rather than a defect fix. |
| `mcp-93` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #93 | low | correctness | **Repair item fixes.** Low fatigue improves repair chance instead of reducing it; bound items can be repaired. |
| `mcp-96` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #96 | low | compat | **Weapon reach issues.** Allows modded weapon reach below 1.0 and applies hand-to-hand reach to the player. |
| `mcp-99` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #99 | low | correctness | **Light spell fix.** Light effect is three times too bright with a poor falloff. Visual; confirm on the Xbox renderer. |
| `mcp-100` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #100 | low | compat | **Allow faction leaving.** Script or modding support; worth porting only when a mod used on Xbox needs it. |
| `mcp-101` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #101 | low | compat | **Slow movement anim fix.** Very slow walk animations cause animation errors; only for animation mods. |
| `mcp-103` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #103 | low | compat | **PlaySoundVP volume fix.** PlaySoundVP ignores its volume argument. |
| `mcp-104` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #104 | low | correctness | **Fix enchant options on ranged.** Cast on Strike offered for bows, where it can never trigger. |
| `mcp-105` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #105 | low | compat | **Prevent empty messages.** Suppress empty notification messages, which mods use to silence messages. |
| `mcp-106` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #106 | low | balance | **Hidden locks.** Balance change, opt in only; MCP ships it as an option rather than a defect fix. |
| `mcp-108` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #108 | low | balance | **Attribute uncap.** Attribute uncap. Balance, and incompatible with levelling mods that expect a cap. |
| `mcp-109` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #109 | low | correctness | **Hit fader fix.** The red damage overlay can stick across a cell change. |
| `mcp-110` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #110 | low | balance | **Skill uncap.** Skill uncap. Balance, and incompatible with levelling mods that expect a cap. |
| `mcp-111` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #111 | low | balance | **Weapon resistance change.** Balance change, opt in only; MCP ships it as an option rather than a defect fix. |
| `mcp-112` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #112 | low | correctness | **Disintegrate fix.** Disintegrate Weapon and Armor apply little or no damage. |
| `mcp-114` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #114 | low | compat (PfP profiles only) | **Separate axe inventory sounds.** MCP source category is **Mod specific**. Patch for Purists documents it as a requirement; prioritize only for profiles that use PfP. |
| `mcp-115` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #115 | low | correctness | **Confiscated item fix.** Guards and merchants confiscate items that are no longer stolen. |
| `mcp-116` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #116 | low | correctness | **Water environment sound fix.** Underwater sound lost through doors between underwater areas; wrong water ambience. |
| `mcp-117` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #117 | low | compat | **'Talked to PC' extension.** Script or modding support; worth porting only when a mod used on Xbox needs it. |
| `mcp-119` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #119 | low | compat (PfP profiles only) | **Creature voiceover enable.** MCP source category is **Mod specific**. Required by PfP and one of the few named-mod patches relevant to the current library. |
| `mcp-120` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #120 | low | compat | **Lock level scripting.** Script or modding support; worth porting only when a mod used on Xbox needs it. |
| `mcp-121` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #121 | low | balance | **Self-enchanting fix.** Self-enchanting with several effects fails far more often than intended. |
| `mcp-122` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #122 | low | correctness | **Ammunition fix.** The ammunition counter does not update while an inventory filter hides the ammo. |
| `mcp-126` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #126 | low | correctness | **Telekinesis fix.** Telekinesis does not reach lights, ammunition, books and activators. |
| `mcp-130` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #130 | low | qol | **Over-the-shoulder third person camera.** Over-the-shoulder third-person camera. |
| `mcp-132` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #132 | low | compat | **Improved animation support.** Animation mod support: extra bones, PlayGroup and LoopGroup fixes. Needed only by animation mods. |
| `mcp-133` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #133 | low | qol | **Reduce camera clipping.** Less third-person camera snapping past NPCs and activators. |
| `mcp-134` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #134 | low | correctness | **NPC minor behaviour fixes.** Minor AI faults: short flee distance, knocked-down followers. |
| `mcp-135` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #135 | low | correctness | **Ammo fixes.** Ammunition tooltips omit damage; ammo can be consumed when not loaded. |
| `mcp-137` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #137 | low | undecided | **Slowfall overhaul.** Mixed: removes slowfall's framerate dependence, a real defect at Xbox framerates, and adds scaled fall damage, a balance change. Split before porting. |
| `mcp-139` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #139 | low | correctness | **Barter gold reset fix.** Merchant gold resets only when the barter window opens. |
| `mcp-142` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #142 | low | balance | **Two-handed weapon removes shield.** Balance change, opt in only; MCP ships it as an option rather than a defect fix. |
| `mcp-143` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #143 | low | compat | **Get/SetAngle enhancement.** Script or modding support; worth porting only when a mod used on Xbox needs it. |
| `mcp-144` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #144 | low | qol | **Better recharging.** Simpler recharging: pick the item, then the soul gem. |
| `mcp-145` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #145 | low | qol | **Quality-based potion icons/models.** Potion icons and models reflect quality instead of being random. |
| `mcp-147` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #147 | low | balance | **Healthy appetite.** Balance change, opt in only; MCP ships it as an option rather than a defect fix. |
| `mcp-148` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #148 | low | qol | **Spellmaker/enchanting improvement.** Spellmaker magnitude controls that keep minimum and maximum consistent. |
| `mcp-150` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #150 | low | balance (opt in) | **Shield hit-location skill credit.** Alters skill progression even if the original hit attribution is wrong. |
| `mcp-152` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #152 | low | compat | **First person swim animations.** Script or modding support; worth porting only when a mod used on Xbox needs it. |
| `mcp-155` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #155 | low | balance (opt in) | **Sneaking boots penalty.** The GMST suggests intent, but enabling it changes stealth balance. |
| `mcp-156` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #156 | low | balance | **Spellmaking matches editor.** Balance change, opt in only; MCP ships it as an option rather than a defect fix. |
| `mcp-157` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #157 | low | balance | **Allow gloves with bracers.** Balance change, opt in only; MCP ships it as an option rather than a defect fix. |
| `mcp-158` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #158 | low | compat | **GetWeaponType fix.** GetWeaponType reports a short blade for lockpicks and probes. |
| `mcp-159` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #159 | low | correctness | **Probe quality fix.** Probe quality never reduces trap complexity. |
| `mcp-161` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #161 | low | compat | **GetSpellEffects tweak.** Script or modding support; worth porting only when a mod used on Xbox needs it. |
| `mcp-162` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #162 | low | qol | **See all standard potion effects.** Show every effect on standard potions regardless of alchemy skill. |
| `mcp-163` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #163 | low | correctness | **Bow sound glitch fix.** Bow sounds repeat if the player moves after drawing. |
| `mcp-164` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #164 | low | compat (opt in) | **Read local variables of a running global script.** Three single-byte edits. This widens what scripts may do rather than correcting a defect, so it is compatibility support, not a default. Directly relevant to the `--apply script-ext` surface; enable for profiles whose mods need 32-bit longs shared without float rounding. |
| `mcp-165` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #165 | low | compat | **AIActivate enhancement.** Script or modding support; worth porting only when a mod used on Xbox needs it. |
| `mcp-166` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #166 | low | compat | **Scriptable potion use.** Script or modding support; worth porting only when a mod used on Xbox needs it. |
| `mcp-167` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #167 | low | compat | **Container respawn timescale.** Script or modding support; worth porting only when a mod used on Xbox needs it. |
| `mcp-168` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #168 | low | correctness | **Avoid blame for neutral NPC deaths.** The player is blamed for neutral NPCs killed by others' area spells. |
| `mcp-169` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #169 | low | correctness | **Multiple summons overlap fix.** Several creatures summoned together stack on one spot. |
| `mcp-170` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #170 | low | correctness | **Barter haggle fix.** Holding the haggle buttons stops working at high framerates, which the Xbox rarely reaches. Check whether it reproduces at all. |
| `mcp-171` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #171 | low | correctness | **Menu mode world interaction fix.** World items can be picked up while crafting menus are open. |
| `mcp-172` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #172 | low | balance | **Enchanted item rebalance.** Balance change, opt in only; MCP ships it as an option rather than a defect fix. |
| `mcp-174` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #174 | low | compat | **AddItem with levelled items.** Script or modding support; worth porting only when a mod used on Xbox needs it. |
| `mcp-175` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #175 | low | compat | **MoveWorld Z fix.** MoveWorld misuses frame time on the Z axis, so scripted movement varies with framerate. Xbox framerates vary widely. |
| `mcp-176` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #176 | low | correctness | **Spell effect tooltip fix.** Active effect tooltips name the wrong source item. |
| `mcp-179` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #179 | low | balance (opt in) | **Followers defend immediately.** MCP source category is **Game mechanics**. Can make followers chase ranged enemies or enter hazards; OpenMW documents similar behaviour as optional and not extensively tested. Keep it disabled by default because of pursuit and hazard side effects. |
| `mcp-258` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #258 | low | qol | **Disable map smoothing.** Pixel-filtered world map instead of blurred. |
| `mcp-259` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #259 | low | qol | **Display more accurate item weight.** Item weight to two decimal places. |
| `mcp-262` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #262 | low | qol | **Larger service/chargen menus.** Larger service and character-generation menus; may help legibility on a TV. |
| `mcp-265` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #265 | low | compat | **Journal text colour configuration.** Journal text colour from Morrowind.ini, for replacement fonts. The Xbox reads the ini too. |
| `mcp-269` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #269 | low | qol | **Better ingredient and item selector.** A large labelled list for choosing ingredients and items. |
| `mcp-270` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #270 | low | qol | **Better typography.** Book, scroll and journal layout with more text per page. The Xbox fonts and screen differ; check legibility. |
| `mcp-271` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #271 | low | qol | **Persuasion improvement.** Persuasion menu opens at the pointer and stays open. |
| `mcp-272` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #272 | low | qol | **Improved inventory filters.** Tidier inventory filters. |
| `mcp-274` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #274 | low | qol | **Better spell merchants.** Spell merchants show magicka cost and hide unsellable powers. |
| `mcp-275` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #275 | low | qol | **Level-up skills tooltip.** Level tooltip shows skill increases per attribute. |
| `mcp-276` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #276 | low | qol | **Convenient defaults.** Start loaded games running and on the world map. |
| `mcp-277` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #277 | low | qol | **Better haggling.** Faster haggling input. |
| `mwse-activation-skinned-ray` | [MWSE](https://mwse.github.io/MWSE/references/general/patches/) | low | correctness (recommended) | **Make activation ray tests work against affected skinned objects.** Renderer/scene-graph dependent; use a concrete inaccessible-object reproducer. |
| `mwse-anim-idle-sync` | [MWSE](https://mwse.github.io/MWSE/references/general/patches/) | low | correctness (recommended) | **Keep idle animation selection/state synchronized.** Confirm visible Xbox symptom and save/load interaction. |
| `mwse-book-weapon-enchant` | [MWSE](https://mwse.github.io/MWSE/references/general/patches/) | low | correctness (recommended) | **Preserve weapon-enchantment data through book/object copy paths.** Useful for scripted object copying; verify the affected record classes. |
| `mwse-invis-chameleon` | [MWSE](https://mwse.github.io/MWSE/references/general/patches/) | low | correctness (recommended) | **Keep invisibility/chameleon state synchronized with visuals and actor state.** Define whether the defect is visual only or also affects detection. |
| `mwse-mobile-projectile` | [MWSE](https://mwse.github.io/MWSE/references/general/patches/) | low | compat (profile dependent) | **Fully initialize projectiles created through mobile/scripted paths.** Relevant to future script extensions; avoid duplicating the existing arrow-damage display mod. |
| `mwse-sound-loop-volume` | [MWSE](https://mwse.github.io/MWSE/references/general/patches/) | low | correctness (recommended) | **Correct volume updates for looping sounds.** Xbox audio backend may differ; start from the game-side volume state. |
| `mwse-time-midnight` | [MWSE](https://mwse.github.io/MWSE/references/general/patches/) | low | correctness (recommended) | **Avoid truncation/error when crossing midnight.** Test calendar, weather and scheduled-script transitions together. |
| `mwse-time-rest-drift` | [MWSE](https://mwse.github.io/MWSE/references/general/patches/) | low | correctness (recommended) | **Prevent precision drift while advancing time during rest/travel.** Compare day/hour state over repeated long rests. |
| `sigf-skill-gmst` | [SIGF](https://www.nexusmods.com/morrowind/mods/48029) | low | correctness (recommended) | **Use `iLevelupMajorMult` for major skills and `iLevelupMinorMult` for minor skills.** The engine reportedly uses the minor value for major skills and the major value for minor skills. Vanilla values hide much of the error; modded GMSTs expose it. Confirm in the Xbox formula. |
| `xbox-controller-tuning` | [OpenMW](https://openmw.org) | low | qol (opt in) | **Independent horizontal/vertical sensitivity and dead-zone controls.** The Xbox build already reads controller settings from `[Xbox]`; prefer compatible INI keys and no save-format change. |
| `xbox-enchant-chance` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #151 | low | qol (opt in) | **Show self-enchanting success chance.** Useful information, but the PC UI patch does not port directly. Design for Xbox screen space and controller flow. |
| `xbox-ownership` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #273 | low | qol (opt in) | **Ownership indicator/tooltip before taking an item.** Helpful, but it changes information available to the player and the PC tooltip layout is inapplicable. |
| `xbox-weapon-details` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) | low | qol (opt in) | **Expose relevant reach/speed/damage details more clearly.** Inventory space is constrained. Do not reproduce data already shown by `XboxArrowDmgDisp.esp`. |

## deferred

A real fix, held back for evidence, prerequisites or risk.

| fix | from | priority | category | notes |
|---|---|---|---|---|
| `mcp-23` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #23 | medium | undecided | **Inventory bugs group.** Split into individual defects after decoding; do not import a 338-byte group under one policy decision. |
| `mwse-terrain-textures` | [MWSE](https://mwse.github.io/MWSE/references/general/patches/) | medium | undecided | **More-than-500 terrain textures / huge-landmass compatibility.** Potentially useful for TR-sized worlds, but those profiles already exceed the present 64 MB target. Revisit only with a feasible large-landmass profile. |
| `mcp-11` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #11 | low | correctness (undecided) | **Transparent clothes in inventory.** Xbox inventory rendering differs. Only pursue after confirming the affected path exists. |
| `mcp-27` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #27 | low | correctness | **Lighting fixes group.** A group of renderer lighting fixes. Split into single defects after decoding, as with MCP-23, and confirm each on the Xbox renderer. |
| `mcp-35` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #35 | low | undecided | **Reflection on skinned models.** Xbox GPU/render path differs. Require a reproduced Xbox defect and a console-appropriate implementation. |
| `mcp-54` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #54 | low | compat | **Hi-def cutscene support.** Cutscenes up to 2048x1024. Unlikely to fit the Xbox memory budget; Xbox builds should ship Xbox-sized video. |
| `mcp-113` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #113 | low | correctness (undecided) | **Incorrect inventory sounds.** Large patch with Xbox UI/audio-path uncertainty. Decode before accepting. |
| `mcp-124` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #124 | low | balance (no default) | **Creature magicka/fatigue scaling.** Experimental inferred intent which introduces new scaling. Require a design decision, not just binary parity. |
| `mcp-128` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #128 | low | qol | **Rain/snow collision.** Rain and snow stop at roofs, at a CPU cost MCP itself warns about. Benchmark on the 733 MHz CPU before considering it. |
| `mcp-131` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #131 | low | correctness | **Bump/reflect map local lighting.** Bump and reflection maps ignore local light. Renderer-specific; the Xbox renderer differs. |
| `mgexe-emissive-particles` | [MGE XE](https://www.nexusmods.com/morrowind/mods/41102) | low | undecided | **Emissive-particle rendering correction.** Renderer-specific. Establish that the Xbox renderer shares the defect before doing binary work. |
| `mwse-empty-menu-position` | [MWSE](https://mwse.github.io/MWSE/references/general/patches/) | low | undecided | **Empty menu position and long derived-key menu crashes.** PC UI evidence is insufficient; prove the Xbox menu path first. |
| `xbox-fmap-save` | TES3X | low | performance (deferred pending a format decision) | **Shrink or deduplicate the fixed map bitmap written into every save.** `FMAP` is 786,472 bytes and byte-identical across all 18 console saves - 49.5% of each file and twice the next-largest record. Any change alters the save format, so it breaks Wrye Mash and existing saves; that cost has to be accepted before implementation work, not after. Settle the save-size ceiling question (`MCP-58`) first, because without a threshold there is nothing to measure the gain against. |

## not-applicable

Targets PC-only code or behaviour; there is nothing to fix on the Xbox.

| fix | from | priority | category | notes |
|---|---|---|---|---|
| `mcp-39` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #39 | none | undecided | **Splash/title quality.** PC presentation path; solve Xbox assets directly if a real issue appears. |
| `mcp-45` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #45 | none | qol | **Toggle sneak.** The Xbox controller path already toggles sneak on left-thumb click; MCP adds that behaviour to the PC key path. |
| `mcp-82` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #82 | none |  | **Shortcut key improvements.** Keyboard shortcuts such as closing dialogue with the space bar; the Xbox has no keyboard in play. |
| `mcp-107` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #107 | none |  | **Don't loot on dispose.** Ctrl-click to dispose without looting. Needs a keyboard modifier; a controller version would be a new design. |
| `mcp-136` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #136 | none | undecided | **Resolution options.** PC display-mode path; no direct Xbox value. |
| `mcp-153` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #153 | none | undecided | **Mouse cursor movement.** PC mouse input path. |
| `mcp-257` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #257 | none |  | **Main menu wider textures.** Wider main menu textures for translated PC editions; TES3X targets the US English Xbox release. |
| `mcp-260` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #260 | none |  | **Polish keyboard support.** Polish keyboard input on PC. |
| `mcp-261` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #261 | none |  | **Un-restrict menu size.** Removes limits on resizing menus with the mouse; Xbox menus are not resized by the player. |
| `mcp-263` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #263 | none |  | **Japanese localization.** Japanese fonts for the PC edition. |
| `mcp-264` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #264 | none |  | **Russian fixes.** Fixes for the Russian PC localisation. |
| `mcp-266` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #266 | none |  | **Polish character corrections.** Polish characters with a PC font mod. |
| `mcp-268` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #268 | none |  | **Spell select by name.** Select spells by typing their first letter; needs a keyboard. |
| `mgexe-high-fps-input` | [MGE XE](https://www.nexusmods.com/morrowind/mods/41102) | none | undecided | **High-FPS mouse/input-drop fix.** PC input and frame-pacing path unless an Xbox controller reproducer says otherwise. |
| `mwse-pc-process` | [MWSE](https://mwse.github.io/MWSE/references/general/patches/) | none | undecided | **Symbolic-link and quit-with-message-box/background-process fixes.** PC filesystem/process behaviour. |

## rejected

Deliberately left out of TES3X.

| fix | from | priority | category | notes |
|---|---|---|---|---|
| `mcp-140` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #140 | none | performance | **Throttle loading-screen redraws.** Original-Xbox cold New Game A/B runs with caches cleared averaged 99.3495 seconds control and 99.3860 seconds patched. The 36.5 ms difference was smaller than the 230-339 ms repeat variation, so the port had no measurable benefit. |
| `openmw-parity` | [OpenMW](https://openmw.org) | none | undecided | **Blanket OpenMW parity.** OpenMW is a separate reimplementation and intentionally diverges in places. Only explicit vanilla defects or desired, scoped features enter this ledger. |
