# Patches

Generated from [`patches.toml`](../patches.toml) by `tools/tes3x_patches.py --write`.
Edit that file, not this one.

## Implemented

**Status**: `implemented` means the patch applies and passes structural checks;
`verified-xemu` means it was shown to work in the xemu emulator; `verified-hardware`
means it was shown to work on an original Xbox.

**Selected by**: `standard` and `development` are presets; a patch marked `standard` is
also in `development`. Anything else is enabled by name in a profile.

| patch | what it does | from | category | status | selected by |
|---|---|---|---|---|---|
| `payload=FILE.pe` | Inject a code section and run it from the entry point. | TES3X | infrastructure | verified-xemu | command line |
| `boot-media` | Permit booting from any media and region, not just a retail DVD. | TES3X | infrastructure | verified-xemu | every build |
| `drive-letters=LETTER` | Point every Data Files asset path at one drive. | TES3X | infrastructure | verified-xemu | every build |
| `save-staging=LETTER` | Stage saves on one volume with UDATA so the commit renames instead of copying. | TES3X | infrastructure | implemented | command line |
| `title=NAME` | Rename the image, so parallel installs are told apart in a dashboard. | TES3X | infrastructure | implemented | command line |
| `multi-bsa` | Load every archive listed in tes3xarch.txt, not just Morrowind.bsa. | TES3X | infrastructure | verified-xemu | delta-bsa packing |
| `script-ext` | Add script opcodes: widen the length bounds and hook Script::RunFunction. | TES3X | compat | verified-xemu | by name |
| `mcp-1` | Stop an unresolvable reference being restamped as created at runtime. | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #1 | core | implemented | by name |
| `mcp-97` | Advance the script parser correctly while initializing saved data. | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #97 | core | verified-xemu | standard |
| `mcp-154` | Pad compiled script-data allocations to keep dword reads in bounds. | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #154 | core | implemented | by name |
| `mcp-102` | Reactivate script-triggered objects after their script mod is removed. | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #102 | correctness | verified-xemu | standard |
| `mcp-140` | Throttle loading-screen redraws to one every 50 milliseconds. | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #140 | performance | implemented | by name |
| `rotating-autosaves` | Rotate automatic saves through INI-configurable slots. | [OpenMW](https://openmw.org) | qol | implemented | by name |
| `diagnostics` | Enable INI-controlled crash records, snapshots and a hang watchdog. | TES3X | instrumentation | verified-xemu | development |
| `console` | Make the in-game console reachable, by replacing its input gate. | TES3X | qol | verified-xemu | development |
| `profile=VA[,VA...]` | Time listed functions with RDTSC at every direct call site. | TES3X | instrumentation | implemented | command line |

## Not implemented

Fixes from other projects that have been reviewed for the Xbox, and why each is not
implemented yet or at all. Appearing here does not mean the Xbox build has the defect.
A fix from another project that is not listed has not been reviewed yet.

### researching

Being decoded or reproduced now.

| fix | from | category | notes |
|---|---|---|---|
| `mcp-58` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #58 | correctness (undecided) | **Save-count warning.** MCP warns before PC memory corruption at roughly 300 save files. Xbox uses `XCreateSaveGame` and different enumeration, so first determine whether any equivalent limit exists; do not port the PC threshold by assumption. |

### candidate

Worth porting, but not yet shown to affect the Xbox build.

| fix | from | category | notes |
|---|---|---|---|
| `mcp-98` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #98 | core (intended default) | **Animated container refcount crash.** Small crash fix; reproduce with a merchant and an animated container in the same cell. |
| `mcp-37` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #37 | core (intended default) | **Delayed spell crash.** Crash can occur roughly 72 game hours after the initiating cell change, easily misdiagnosed as save corruption. |
| `mcp-92` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #92 | core (intended default) | **Summoned creature dangling-reference crash.** Reproduce unsummoning through non-death paths and locate the lifetime transition. |
| `mcp-178` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #178 | core (intended default) | **Load-warning input crash.** Xbox has load warnings and a single controller path, so this is directly testable. |
| `mcp-125` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #125 | core (intended default) | **Position/PositionCell uninitialized-script crash.** Moving an NPC to an unvisited cell can leave its script uninitialized and make dialogue crash. |
| `mcp-149` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #149 | core (intended default) | **Dispose corpse before death processing completes.** An actor is disposable before its final processing runs, so `OnDeath`/`OnMurder` can never fire and some spell effects never expire. Unrecoverable in an existing save, and corpse looting/disposal is part of the console avoidance ritual, so Xbox exposure is high. Reproduce by disposing during the death animation. |
| `mcp-118` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #118 | core (intended default) | **Items dropped while levitating or falling are lost.** Dropping in third person while airborne places the object near `{0, 0, 0}`, which is normally unreachable. Data loss, not a preference. Confirm the Xbox third-person and levitation paths share the defect. |
| `mcp-141` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #141 | core (intended default) | **Script expression parser / `Random` fix.** `Random` on multiples of 256 can crash and corrupt its argument. Large, but directly relevant to the extended script surface. |
| `mcp-123` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #123 | core (intended default) | **Persist `PlaceItem` objects in unvisited cells.** Missing dirty mark loses placed objects. This is persistence/data loss rather than a gameplay preference. |
| `mcp-173` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #173 | core (intended default) | **Persist `RaiseRank`/`LowerRank`.** Missing dirty mark can break faction quests after reload. |
| `mcp-138` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #138 | correctness (recommended) | **Reliable `CellChanged` for scripted teleports.** Script-contract fix used by mods; preserve vanilla transition semantics outside the missed path. |
| `mcp-46a` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #46 | correctness (recommended) | **`Drop` places at the acting reference, not the player.** The engine treats every drop as if it came from the player inventory view, so scripted drops land at the wrong actor. Split from the rest of id 46 under rule 3. |
| `mcp-46b` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #46 | correctness (recommended) | **`PlaceAtPC` distance in third person, `PlaceAtMe` scale inheritance.** A line-of-sight test misplaces third-person `PlaceAtPC` at the player's feet; `PlaceAtMe` inherits a target's rotation but not its scale. Two separate defects sharing id 46's record set. |
| `mcp-164` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #164 | compat (opt in) | **Read local variables of a running global script.** Three single-byte edits. This widens what scripts may do rather than correcting a defect, so it is compatibility support, not a default. Directly relevant to the `--apply script-ext` surface; enable for profiles whose mods need 32-bit longs shared without float rounding. |
| `mcp-2a` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #2 | correctness (recommended) | **Fix inverted Mercantile contribution to price.** Isolate the formula error. |
| `mcp-2b` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #2 | balance (opt in) | **Reduce all price modifiers by 50% and alter zero-value handling.** Rebalance, not required to correct the inversion. Do not couple it to 2a. |
| `mcp-5` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #5 | balance (opt in) | **Stop merchants equipping sold items.** Changes visible merchant behaviour. OpenMW exposes comparable behaviour as a disabled-by-default option. |
| `mcp-8` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #8 | balance (opt in) | **Calm spell behaviour.** Arguable intended behaviour, but OpenMW keeps classic behaviour by default. |
| `mcp-10` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #10 | balance (opt in) | **Reflected spell behaviour.** MCP's rationale includes making reflection less safe; that is balance as well as correctness. OpenMW also preserves classic behaviour by default. |
| `mcp-24a` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #24 | correctness (recommended if reproduced) | **Prevent pathological NPC potion-use frequency.** Separate a bad scheduling/throttle path from new AI. |
| `mcp-24b` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #24 | balance (opt in) | **Add magicka-potion decision-making to NPC AI.** New combat AI behaviour. |
| `mcp-33` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #33 | balance (opt in) | **Training price/stats.** Formula looks incorrect, but trainer base-skill behaviour is configurable in OpenMW and affects progression/economy. |
| `mcp-34a` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #34 | correctness (recommended) | **Make displayed and charged travel prices agree.** A direct UI/transaction mismatch. |
| `mcp-34b` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #34 | balance (opt in) | **Charge separately for companions.** Economy choice; keep separate from the mismatch fix. |
| `mcp-80` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #80 | balance (opt in) | **Drain Intelligence magicka exploit.** Exploit and balance change, not stability. |
| `mcp-150` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #150 | balance (opt in) | **Shield hit-location skill credit.** Alters skill progression even if the original hit attribution is wrong. |
| `mcp-155` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #155 | balance (opt in) | **Sneaking boots penalty.** The GMST suggests intent, but enabling it changes stealth balance. |
| `mcp-179` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #179 | balance (opt in) | **Followers defend immediately.** MCP source category is **Game mechanics**. Can make followers chase ranged enemies or enter hazards; OpenMW documents similar behaviour as optional and not extensively tested. Keep it disabled by default because of pursuit and hazard side effects. |
| `mcp-114` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #114 | compat (PfP profiles only) | **Separate axe inventory sounds.** MCP source category is **Mod specific**. Patch for Purists documents it as a requirement; prioritize only for profiles that use PfP. |
| `mcp-119` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #119 | compat (PfP profiles only) | **Creature voiceover enable.** MCP source category is **Mod specific**. Required by PfP and one of the few named-mod patches relevant to the current library. |
| `mwse-vfx-save-empty` | [MWSE](https://mwse.github.io/MWSE/references/general/patches/) | core (intended default) | **Avoid crash while saving an active visual effect when none of its particles can be serialized.** Build a small spell/VFX save reproducer before locating the serializer. |
| `mwse-vfx-load-bloat` | [MWSE](https://mwse.github.io/MWSE/references/general/patches/) | core (intended default) | **Reject or repair invalid loaded VFX state which causes load errors and save growth.** Especially relevant to long-lived Xbox saves; measure record growth as well as crash behaviour. |
| `mwse-pathgrid-zero` | [MWSE](https://mwse.github.io/MWSE/references/general/patches/) | core (intended default) | **Avoid NPC flee selecting a random node from a zero-node pathgrid.** Clean bounds case and likely inexpensive port once the AI path is located. |
| `mwse-enable-disable-vars` | [MWSE](https://mwse.github.io/MWSE/references/general/patches/) | core (intended default) | **Avoid `Enable`/`Disable` crash when script variables are not initialized.** High script/mod relevance. Test local, global and absent script data. |
| `mwse-uncloned-removeitem` | [MWSE](https://mwse.github.io/MWSE/references/general/patches/) | core (intended default) | **Avoid actor/item-removal crash on uncloned actors.** Establish exact actor lifetime and inventory precondition. |
| `mwse-paperdoll` | [MWSE](https://mwse.github.io/MWSE/references/general/patches/) | core (intended default if Xbox path exists) | **Avoid paper-doll equip/unequip crash.** Xbox inventory rendering may not use the PC paper-doll path; prove applicability first. |
| `mwse-map-border-marker` | [MWSE](https://mwse.github.io/MWSE/references/general/patches/) | core (intended default) | **Avoid crash while updating map markers at the drawable map border.** Relevant to map expansion and large landmasses if the Xbox map bounds match. |
| `mwse-clone-stringextra` | [MWSE](https://mwse.github.io/MWSE/references/general/patches/) | core (profile-dependent priority) | **Correct `NiStringExtraData` cloning.** Mesh-driven lifetime bug; prioritize if a current asset reproduces it. |
| `mwse-clone-sortadjust` | [MWSE](https://mwse.github.io/MWSE/references/general/patches/) | core (profile-dependent priority) | **Correct `NiSortAdjustNode` cloning.** Same rule: a mod-library reproducer raises priority. |
| `mwse-clone-light-null` | [MWSE](https://mwse.github.io/MWSE/references/general/patches/) | core (profile-dependent priority) | **Handle missing light data while cloning.** Validate against malformed or legal-but-unusual meshes. |
| `omw-live-inventory` | [OpenMW-4642](https://gitlab.com/OpenMW/openmw/-/merge_requests/4642) | correctness (recommended) | **Refresh an open container/inventory after script `AddItem`/`RemoveItem`.** The reported vanilla defect leaves the menu stale and can permit duplication. Reproduce with the Xbox menu before choosing a hook; promote to `core` if data loss/duplication is confirmed. |
| `sigf-skill-gmst` | [SIGF](https://www.nexusmods.com/morrowind/mods/48029) | correctness (recommended) | **Use `iLevelupMajorMult` for major skills and `iLevelupMinorMult` for minor skills.** The engine reportedly uses the minor value for major skills and the major value for minor skills. Vanilla values hide much of the error; modded GMSTs expose it. Confirm in the Xbox formula. |
| `mwse-time-rest-drift` | [MWSE](https://mwse.github.io/MWSE/references/general/patches/) | correctness (recommended) | **Prevent precision drift while advancing time during rest/travel.** Compare day/hour state over repeated long rests. |
| `mwse-time-midnight` | [MWSE](https://mwse.github.io/MWSE/references/general/patches/) | correctness (recommended) | **Avoid truncation/error when crossing midnight.** Test calendar, weather and scheduled-script transitions together. |
| `mwse-invis-chameleon` | [MWSE](https://mwse.github.io/MWSE/references/general/patches/) | correctness (recommended) | **Keep invisibility/chameleon state synchronized with visuals and actor state.** Define whether the defect is visual only or also affects detection. |
| `mwse-sound-loop-volume` | [MWSE](https://mwse.github.io/MWSE/references/general/patches/) | correctness (recommended) | **Correct volume updates for looping sounds.** Xbox audio backend may differ; start from the game-side volume state. |
| `mwse-activation-skinned-ray` | [MWSE](https://mwse.github.io/MWSE/references/general/patches/) | correctness (recommended) | **Make activation ray tests work against affected skinned objects.** Renderer/scene-graph dependent; use a concrete inaccessible-object reproducer. |
| `mwse-anim-idle-sync` | [MWSE](https://mwse.github.io/MWSE/references/general/patches/) | correctness (recommended) | **Keep idle animation selection/state synchronized.** Confirm visible Xbox symptom and save/load interaction. |
| `mwse-book-weapon-enchant` | [MWSE](https://mwse.github.io/MWSE/references/general/patches/) | correctness (recommended) | **Preserve weapon-enchantment data through book/object copy paths.** Useful for scripted object copying; verify the affected record classes. |
| `mwse-mobile-projectile` | [MWSE](https://mwse.github.io/MWSE/references/general/patches/) | compat (profile dependent) | **Fully initialize projectiles created through mobile/scripted paths.** Relevant to future script extensions; avoid duplicating the existing arrow-damage display mod. |
| `mwse-dialogue-filter` | [MWSE](https://mwse.github.io/MWSE/references/general/patches/) | performance (benchmark first) | **Replace dialogue filtering/search with a more efficient implementation.** High potential with PfP's dialogue-heavy master. Measure dialogue-open latency and memory with/without PfP, then regression-test filtering. |
| `mwse-journal-update` | [MWSE](https://mwse.github.io/MWSE/references/general/patches/) | performance (benchmark first) | **Optimize journal update/search work.** Measure large-journal open/update time and verify ordering/quest indices. |
| `mwse-showmap-fillmap` | [MWSE](https://mwse.github.io/MWSE/references/general/patches/) | performance (benchmark first) | **Optimize `ShowMap`/`FillMap`.** Compare command time and map correctness across repeated calls. |
| `mwse-global-access` | [MWSE](https://mwse.github.io/MWSE/references/general/patches/) | performance (benchmark first) | **Speed script global-variable lookup.** Existing profiling found only about 22 script commands/frame in the baseline; require evidence this lookup matters in a real modded profile. |
| `mwse-getdeadcount` | [MWSE](https://mwse.github.io/MWSE/references/general/patches/) | performance (benchmark first) | **Avoid repeated expensive work in `GetDeadCount`.** Profile scripts which call it frequently and preserve dynamic death counts. |
| `mwse-disposition-filter` | [MWSE](https://mwse.github.io/MWSE/references/general/patches/) | performance (benchmark first) | **Optimize disposition/dialogue filtering.** Test result identity, not just speed, against a dialogue-rich load order. |
| `mwse-ini-cache` | [MWSE](https://mwse.github.io/MWSE/references/general/patches/) | performance (benchmark first) | **Cache repeated `DontThreadLoad`/INI reads.** First instrument call frequency. Preserve runtime configuration changes if vanilla observes them. |
| `xbox-controller-tuning` | [OpenMW](https://openmw.org) | qol (opt in) | **Independent horizontal/vertical sensitivity and dead-zone controls.** The Xbox build already reads controller settings from `[Xbox]`; prefer compatible INI keys and no save-format change. |
| `xbox-enchant-chance` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #151 | qol (opt in) | **Show self-enchanting success chance.** Useful information, but the PC UI patch does not port directly. Design for Xbox screen space and controller flow. |
| `xbox-weapon-details` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) | qol (opt in) | **Expose relevant reach/speed/damage details more clearly.** Inventory space is constrained. Do not reproduce data already shown by `XboxArrowDmgDisp.esp`. |
| `xbox-ownership` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #273 | qol (opt in) | **Ownership indicator/tooltip before taking an item.** Helpful, but it changes information available to the player and the PC tooltip layout is inapplicable. |

### deferred

A real fix, held back for evidence, prerequisites or risk.

| fix | from | category | notes |
|---|---|---|---|
| `mcp-113` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #113 | correctness (undecided) | **Incorrect inventory sounds.** Large patch with Xbox UI/audio-path uncertainty. Decode before accepting. |
| `mcp-23` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #23 | undecided | **Inventory bugs group.** Split into individual defects after decoding; do not import a 338-byte group under one policy decision. |
| `mcp-11` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #11 | correctness (undecided) | **Transparent clothes in inventory.** Xbox inventory rendering differs. Only pursue after confirming the affected path exists. |
| `mcp-124` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #124 | balance (no default) | **Creature magicka/fatigue scaling.** Experimental inferred intent which introduces new scaling. Require a design decision, not just binary parity. |
| `xbox-fmap-save` | TES3X | performance (deferred pending a format decision) | **Shrink or deduplicate the fixed map bitmap written into every save.** `FMAP` is 786,472 bytes and byte-identical across all 18 console saves - 49.5% of each file and twice the next-largest record. Any change alters the save format, so it breaks Wrye Mash and existing saves; that cost has to be accepted before implementation work, not after. Settle the save-size ceiling question (`MCP-58`) first, because without a threshold there is nothing to measure the gain against. |
| `mcp-35` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #35 | undecided | **Reflection on skinned models.** Xbox GPU/render path differs. Require a reproduced Xbox defect and a console-appropriate implementation. |
| `mcp-named-mods` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) | undecided | **Patches for specific named mods, other than current dependencies.** Enable only when the named mod enters an Xbox profile and the feature is still needed. |
| `mwse-empty-menu-position` | [MWSE](https://mwse.github.io/MWSE/references/general/patches/) | undecided | **Empty menu position and long derived-key menu crashes.** PC UI evidence is insufficient; prove the Xbox menu path first. |
| `mgexe-emissive-particles` | [MGE XE](https://www.nexusmods.com/morrowind/mods/41102) | undecided | **Emissive-particle rendering correction.** Renderer-specific. Establish that the Xbox renderer shares the defect before doing binary work. |
| `mwse-terrain-textures` | [MWSE](https://mwse.github.io/MWSE/references/general/patches/) | undecided | **More-than-500 terrain textures / huge-landmass compatibility.** Potentially useful for TR-sized worlds, but those profiles already exceed the present 64 MB target. Revisit only with a feasible large-landmass profile. |

### not-applicable

Targets PC-only code or behaviour; there is nothing to fix on the Xbox.

| fix | from | category | notes |
|---|---|---|---|
| `mcp-136` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #136 | undecided | **Resolution options.** PC display-mode path; no direct Xbox value. |
| `mcp-153` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #153 | undecided | **Mouse cursor movement.** PC mouse input path. |
| `mcp-39` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) #39 | undecided | **Splash/title quality.** PC presentation path; solve Xbox assets directly if a real issue appears. |
| `mcp-pc-ui` | [MCP](https://www.nexusmods.com/morrowind/mods/19510) | undecided | **Mouse, desktop UI and international keyboard layout patches.** Do not port by category. A specific Xbox-visible defect may return as a new ledger row. |
| `mwse-pc-process` | [MWSE](https://mwse.github.io/MWSE/references/general/patches/) | undecided | **Symbolic-link and quit-with-message-box/background-process fixes.** PC filesystem/process behaviour. |
| `mgexe-high-fps-input` | [MGE XE](https://www.nexusmods.com/morrowind/mods/41102) | undecided | **High-FPS mouse/input-drop fix.** PC input and frame-pacing path unless an Xbox controller reproducer says otherwise. |

### rejected

Deliberately left out of TES3X.

| fix | from | category | notes |
|---|---|---|---|
| `openmw-parity` | [OpenMW](https://openmw.org) | undecided | **Blanket OpenMW parity.** OpenMW is a separate reimplementation and intentionally diverges in places. Only explicit vanilla defects or desired, scoped features enter this ledger. |
