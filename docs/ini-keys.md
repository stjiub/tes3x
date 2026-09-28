# Morrowind.ini keys the Xbox build reads

Recovered from `morrowind.xbe` by `tools/tes3x_ini.py`, which walks back from every call to the
engine's three ini readers and resolves the pushed section, key and default. **30 sections,
488 keys**; 52 call sites compute a section or key at runtime and are not listed.

These are **stock engine behaviour** - nothing here needs a patch. What the ini cannot do is
enable the console or change any MCP-class defect; those are properties of code. See the findings
entries.

`tools/tes3x_pack.py` writes into the staged ini: `--quickstart [CELL]`, `--show-fps`, and the
general `--ini-set "SECTION:KEY=VALUE"`.

Regenerate with `python tools/tes3x_ini.py <xbe> --all`.

## Keys the payload adds

Injected code reads these through the engine's own reader at `0x001933E0`, so they behave like any
other key. They are listed by patch in [patch settings](patch-settings.md).

## The ones that matter for this project

| key | why |
|---|---|
| `[General] QuickStart` + `Starting Cell` / `Starting Grid X`,`Y` / `Starting Offset X`,`Y` | **adds a button to the menu, it does not skip it** - measured. `0x00200D41` reads it, then `0x00200D64` creates a widget. Still one press instead of navigating; where that button lands you is presumably the `Starting Cell` family, untested |
| `[General] Show FPS`, `Max FPS` | the engine's own frame counter and cap - no injected code |
| `[General] DontThreadLoad`, `ThreadPriority`, `ThreadSleepTime` | loading behaviour, on a console where loading is the complaint |
| `[General] Exterior Cell Buffer`, `Interior Cell Buffer` | cell cache sizing against 64 MB |
| `[General] TryArchiveFirst` | already set by `tes3x_pack`; confirmed a real read, not scene lore |
| `[General] SkipProgramFlows`, `[Movies] Morrowind Logo`, `New Game` | boot-time video and flow skipping. A movie name that does not exist is skipped with a warning (`0x0009E246`); pointing both movies at one skips them |
| `[Debug] No Reboot On New Game`, `No Reboot On Load Game` | the title relaunch; Xbox-only, absent from the PC ini. Set to `1`, New Game continues in the main menu's process instead of relaunching and loading every master again - measured. Load untested |
| `[Xbox] MenuDeadZone`, `RunZone` | the only two keys in the platform's own section |
| `[PreLoad] Cell 0` | preloaded cell list |

## Every section

### `[FontColor]` - 49 keys

| key | type | default |
|---|---|---|
| `color_active` | str | `255,255,255` |
| `color_active_over` | str | `255,255,255` |
| `color_active_pressed` | str | `255,255,255` |
| `color_answer` | str | `255,255,255` |
| `color_answer_over` | str | `255,255,255` |
| `color_answer_pressed` | str | `255,255,255` |
| `color_background` | str | `255,255,255` |
| `color_big_answer` | str | `255,255,255` |
| `color_big_answer_over` | str | `255,255,255` |
| `color_big_answer_pressed` | str | `255,255,255` |
| `color_big_header` | str | `255,255,255` |
| `color_big_link` | str | `255,255,255` |
| `color_big_link_over` | str | `255,255,255` |
| `color_big_link_pressed` | str | `255,255,255` |
| `color_big_normal` | str | `255,255,255` |
| `color_big_normal_over` | str | `255,255,255` |
| `color_big_normal_pressed` | str | `255,255,255` |
| `color_big_notify` | str | `255,255,255` |
| `color_count` | str | `255,255,255` |
| `color_disabled` | str | `255,255,255` |
| `color_disabled_over` | str | `255,255,255` |
| `color_disabled_pressed` | str | `255,255,255` |
| `color_fatigue` | str | `255,255,255` |
| `color_focus` | str | `255,255,255` |
| `color_header` | str | `255,255,255` |
| `color_health` | str | `255,255,255` |
| `color_journal_finished_quest` | str | `60,60,60` |
| `color_journal_finished_quest_over` | str | `100,100,100` |
| `color_journal_finished_quest_pressed` | str | `220,220,220` |
| `color_journal_link` | str | `255,255,255` |
| `color_journal_link_over` | str | `255,255,255` |
| `color_journal_link_pressed` | str | `255,255,255` |
| `color_journal_topic` | str | `255,255,255` |
| `color_journal_topic_over` | str | `255,255,255` |
| `color_journal_topic_pressed` | str | `255,255,255` |
| `color_link` | str | `255,255,255` |
| `color_link_over` | str | `255,255,255` |
| `color_link_pressed` | str | `255,255,255` |
| `color_magic` | str | `255,255,255` |
| `color_magic_fill` | str | `128,128,255` |
| `color_misc` | str | `255,255,255` |
| `color_negative` | str | `255,255,255` |
| `color_normal` | str | `255,255,255` |
| `color_normal_over` | str | `255,255,255` |
| `color_normal_pressed` | str | `255,255,255` |
| `color_notify` | str | `255,255,255` |
| `color_npc_health` | str | `255,186,0` |
| `color_positive` | str | `255,255,255` |
| `color_weapon_fill` | str | `255,128,128` |

### `[Weather]` - 49 keys

| key | type | default |
|---|---|---|
| `AlphaReduce` | str | `1.0` |
| `Ambient Post-Sunrise Time` | str | `2` |
| `Ambient Post-Sunset Time` | str | `0` |
| `Ambient Pre-Sunrise Time` | str | `0` |
| `Ambient Pre-Sunset Time` | str | `2` |
| `BumpFadeColor` | str | `255,255,255,255` |
| `EnvReduceColor` | str | `255,255,255,255` |
| `Fog Depth Change Speed` | str | `1.0` |
| `Fog Post-Sunrise Time` | str | `2` |
| `Fog Post-Sunset Time` | str | `0` |
| `Fog Pre-Sunrise Time` | str | `0` |
| `Fog Pre-Sunset Time` | str | `2` |
| `Hours Between Weather Changes` | str | `12` |
| `LerpCloseColor` | str | `255,255,255,255` |
| `Maximum Time Between Environmental Sounds` | str | `10.0` |
| `Minimum Time Between Environmental Sounds` | str | `1.0` |
| `Precip Gravity` | str | `300` |
| `Rain Ripple Radius` | int |  |
| `Rain Ripple Scale` | str | `1.0` |
| `Rain Ripple Speed` | str | `1.0` |
| `Rain Ripples` | str | `0` |
| `Rain Ripples Per Drop` | int |  |
| `Sky Post-Sunrise Time` | str | `0` |
| `Sky Post-Sunset Time` | str | `0` |
| `Sky Pre-Sunrise Time` | str | `0` |
| `Sky Pre-Sunset Time` | str | `0` |
| `Snow Gravity Scale` | str | `0.25` |
| `Snow High Kill` | str | `700` |
| `Snow Low Kill` | str | `700` |
| `Snow Ripple Radius` | int |  |
| `Snow Ripple Scale` | str | `1.0` |
| `Snow Ripple Speed` | str | `1.0` |
| `Snow Ripples` | str | `0` |
| `Snow Ripples Per Flake` | int |  |
| `Stars Fading Duration` | str | `2` |
| `Stars Post-Sunset Start` | str | `1` |
| `Stars Pre-Sunrise Finish` | str | `2` |
| `Sun Glare Fader Angle Max` | str | `15.0` |
| `Sun Glare Fader Color` | str | `255,255,255` |
| `Sun Glare Fader Max` | str | `0.8` |
| `Sun Post-Sunrise Time` | str | `0` |
| `Sun Post-Sunset Time` | str | `0` |
| `Sun Pre-Sunrise Time` | str | `0` |
| `Sun Pre-Sunset Time` | str | `0` |
| `Sunrise Duration` | str | `2` |
| `Sunrise Time` | str | `6` |
| `Sunset Duration` | str | `2` |
| `Sunset Time` | str | `18` |
| `Timescale Clouds` | int |  |

### `[Weather Thunderstorm]` - 35 keys

| key | type | default |
|---|---|---|
| `Ambient Day Color` | str | `050,050,050` |
| `Ambient Loop Sound ID` | str | `None` |
| `Ambient Night Color` | str | `055,055,055` |
| `Ambient Sunrise Color` | str | `052,052,053` |
| `Ambient Sunset Color` | str | `052,052,053` |
| `Cloud Speed` | str | `10.0` |
| `Cloud Texture` | str | `Tx_Sky_Thunder.tga` |
| `Clouds Maximum Percent` | str | `1.0` |
| `Flash Decrement` | str | `4.00` |
| `Fog Day Color` | str | `055,055,059` |
| `Fog Night Color` | str | `037,037,037` |
| `Fog Sunset Color` | str | `049,049,049` |
| `Glare View` | str | `0` |
| `Land Fog Day Depth` | str | `1.75` |
| `Land Fog Night Depth` | str | `1.75` |
| `Max Raindrops` | str | `200` |
| `Rain Diameter` | str | `300` |
| `Rain Entrance Speed` | str | `10.0` |
| `Rain Height Max` | str | `200` |
| `Rain Height Min` | str | `150` |
| `Rain Loop Sound ID` | str | `Rain Loop` |
| `Rain Threshold` | str | `0.6` |
| `Sky Day Color` | str | `055,055,059` |
| `Sky Night Color` | str | `037,037,037` |
| `Sky Sunset Color` | str | `046,046,048` |
| `Sun Day Color` | str | `065,065,065` |
| `Sun Disc Sunset Color` | str | `255,255,255` |
| `Sun Night Color` | str | `048,048,048` |
| `Sun Sunrise Color` | str |  |
| `Sun Sunset Color` | str |  |
| `Thunder` | str |  |
| `Thunder Frequency` | str | `.10` |
| `Thunder Threshold` | str | `.70` |
| `Transition Delta` | str | `.040` |
| `Wind Speed` | str | `40` |

### `[Weather Rain]` - 32 keys

| key | type | default |
|---|---|---|
| `Ambient Day Color` | str | `060,060,060` |
| `Ambient Loop Sound ID` | str | `None` |
| `Ambient Night Color` | str | `064,064,064` |
| `Ambient Sunrise Color` | str | `062,062,062` |
| `Ambient Sunset Color` | str | `062,062,062` |
| `Cloud Speed` | str | `2.5` |
| `Cloud Texture` | str | `Tx_Sky_Rainy.tga` |
| `Clouds Maximum Percent` | str | `1.0` |
| `Fog Day Color` | str | `072,076,080` |
| `Fog Night Color` | str | `035,035,035` |
| `Fog Sunset Color` | str | `060,067,069` |
| `Glare View` | str | `0` |
| `Land Fog Day Depth` | str | `1.5` |
| `Land Fog Night Depth` | str | `1.5` |
| `Max Raindrops` | str | `200` |
| `Rain Diameter` | str | `300` |
| `Rain Entrance Speed` | str | `5` |
| `Rain Height Max` | str | `200` |
| `Rain Height Min` | str | `150` |
| `Rain Loop Sound ID` | str | `Rain` |
| `Rain Threshold` | str | `.6` |
| `Sky Day Color` | str | `072,076,080` |
| `Sky Night Color` | str | `035,035,035` |
| `Sky Sunrise Color` | str | `053,055,057` |
| `Sky Sunset Color` | str | `053,055,057` |
| `Sun Day Color` | str | `065,065,065` |
| `Sun Disc Sunset Color` | str | `255,255,255` |
| `Sun Night Color` | str | `052,052,052` |
| `Sun Sunrise Color` | str | `058,058,058` |
| `Sun Sunset Color` | str | `058,058,058` |
| `Transition Delta` | str | `.030` |
| `Wind Speed` | str | `15` |

### `[Weather Snow]` - 31 keys

| key | type | default |
|---|---|---|
| `Ambient Day Color` | str | `099,099,099` |
| `Ambient Loop Sound ID` | str | `None` |
| `Ambient Night Color` | str | `067,067,067` |
| `Ambient Sunrise Color` | str | `083,083,083` |
| `Ambient Sunset Color` | str | `083,083,083` |
| `Cloud Speed` | str | `1.5` |
| `Cloud Texture` | str | `Tx_Sky_Snow.tga` |
| `Clouds Maximum Percent` | str | `1.0` |
| `Fog Day Color` | str | `086,080,078` |
| `Fog Night Color` | str | `038,038,038` |
| `Fog Sunset Color` | str | `079,071,070` |
| `Glare View` | str | `0` |
| `Land Fog Day Depth` | str | `2.0` |
| `Land Fog Night Depth` | str | `2.0` |
| `Max Snowflakes` | str | `200` |
| `Sky Day Color` | str | `086,080,078` |
| `Sky Night Color` | str | `038,038,038` |
| `Sky Sunrise Color` | str | `062,059,058` |
| `Sky Sunset Color` | str | `062,059,058` |
| `Snow Diameter` | str | `300` |
| `Snow Entrance Speed` | str | `5` |
| `Snow Height Max` | str | `200` |
| `Snow Height Min` | str | `150` |
| `Snow Threshold` | str | `.6` |
| `Sun Day Color` | str | `110,110,110` |
| `Sun Disc Sunset Color` | str | `255,255,255` |
| `Sun Night Color` | str | `060,060,060` |
| `Sun Sunrise Color` | str | `085,085,085` |
| `Sun Sunset Color` | str | `085,085,085` |
| `Transition Delta` | str | `.030` |
| `Wind Speed` | str | `5` |

### `[General]` - 30 keys

| key | type | default |
|---|---|---|
| `Always Run` | int |  |
| `Clip One To One Float` | int |  |
| `ConsoleBuffer` | int |  |
| `Create Maps Enable` | int |  |
| `DeadFloatScale` | int |  |
| `DisableFOW` | int |  |
| `DontThreadLoad` | int |  |
| `DrawBound` | int |  |
| `Exterior Cell Buffer` | int |  |
| `FakeActiveFile` | str |  |
| `Interior Cell Buffer` | int |  |
| `Map FOW Texture` | str |  |
| `Max FPS` | int |  |
| `Maximum Shadows Per Object` | int |  |
| `Number of Shadows` | int |  |
| `PC Footstep Volume` | str | `1.0f` |
| `QuickStart` | int |  |
| `Show Door Notes` | int |  |
| `Show FPS` | int |  |
| `ShowHitFader` | int |  |
| `SkipProgramFlows` | int |  |
| `Starting Cell` | str |  |
| `Starting Grid X` | int |  |
| `Starting Grid Y` | int |  |
| `Starting Offset X` | int |  |
| `Starting Offset Y` | int |  |
| `ThreadPriority` | str | `-1` |
| `ThreadSleepTime` | int |  |
| `TryArchiveFirst` | int |  |
| `UseAnimationDelta` | int |  |

### `[Water]` - 29 keys

| key | type | default |
|---|---|---|
| `Map Alpha` | str | `0.5` |
| `MaxNumberRipples` | str |  |
| `NearWaterIndoorID` | str | `NearWaterIndoorLoop` |
| `NearWaterIndoorTolerance` | str | `256` |
| `NearWaterOutdoorID` | str | `NearWaterOutoorLoop` |
| `NearWaterOutdoorTolerance` | str | `512` |
| `NearWaterPoints` | str | `8` |
| `NearWaterRadius` | str | `750` |
| `NearWaterUnderwaterFreq` | str | `0.5` |
| `NearWaterUnderwaterVolume` | str | `0.75` |
| `RippleAlphas` | str |  |
| `RippleFrameCount` | str |  |
| `RippleLifetime` | str |  |
| `RippleRotSpeed` | str |  |
| `RippleScale` | str |  |
| `RippleTexture` | str |  |
| `SurfaceFPS` | int |  |
| `SurfaceFrameCount` | str |  |
| `SurfaceTexture` | str |  |
| `SurfaceTileCount` | int |  |
| `TileTextureDivisor` | int |  |
| `UnderwaterColor` | str | `100,175,250` |
| `UnderwaterColorWeight` | str | `0.5` |
| `UnderwaterDayFog` | str | `5` |
| `UnderwaterIndoorFog` | str | `5` |
| `UnderwaterNightFog` | str | `5` |
| `UnderwaterSunriseFog` | str | `5` |
| `UnderwaterSunsetFog` | str | `5` |
| `World Alpha` | str | `1.0` |

### `[Weather Ashstorm]` - 26 keys

| key | type | default |
|---|---|---|
| `Ambient Day Color` | str | `070,044,036` |
| `Ambient Loop Sound ID` | str | `None` |
| `Ambient Night Color` | str | `085,055,043` |
| `Ambient Sunrise Color` | str | `072,050,040` |
| `Ambient Sunset Color` | str | `072,050,040` |
| `Cloud Speed` | str | `12.5` |
| `Cloud Texture` | str | `Tx_Sky_Ash.tga` |
| `Clouds Maximum Percent` | str | `1.0` |
| `Fog Day Color` | str | `094,060,045` |
| `Fog Night Color` | str | `058,036,027` |
| `Fog Sunset Color` | str | `107,056,035` |
| `Glare View` | str | `0` |
| `Land Fog Day Depth` | str | `3.0` |
| `Land Fog Night Depth` | str | `3.0` |
| `Sky Day Color` | str | `094,060,045` |
| `Sky Night Color` | str | `058,036,027` |
| `Sky Sunrise Color` | str | `076,048,036` |
| `Sky Sunset Color` | str | `076,048,036` |
| `Storm Threshold` | str | `.7` |
| `Sun Day Color` | str | `194,105,067` |
| `Sun Disc Sunset Color` | str | `255,255,255` |
| `Sun Night Color` | str | `085,055,043` |
| `Sun Sunrise Color` | str | `140,080,055` |
| `Sun Sunset Color` | str | `140,080,055` |
| `Transition Delta` | str | `.035` |
| `Wind Speed` | str | `60` |

### `[Weather Blizzard]` - 26 keys

| key | type | default |
|---|---|---|
| `Ambient Day Color` | str | `099,099,099` |
| `Ambient Loop Sound ID` | str | `None` |
| `Ambient Night Color` | str | `067,067,067` |
| `Ambient Sunrise Color` | str | `083,083,083` |
| `Ambient Sunset Color` | str | `083,083,083` |
| `Cloud Speed` | str | `1.5` |
| `Cloud Texture` | str | `Tx_Sky_Blizzard.tga` |
| `Clouds Maximum Percent` | str | `1.0` |
| `Fog Day Color` | str | `086,080,078` |
| `Fog Night Color` | str | `038,038,038` |
| `Fog Sunset Color` | str | `079,071,070` |
| `Glare View` | str | `0` |
| `Land Fog Day Depth` | str | `2.0` |
| `Land Fog Night Depth` | str | `2.0` |
| `Sky Day Color` | str | `086,080,078` |
| `Sky Night Color` | str | `038,038,038` |
| `Sky Sunrise Color` | str | `062,059,058` |
| `Sky Sunset Color` | str | `062,059,058` |
| `Storm Threshold` | str | `.5` |
| `Sun Day Color` | str | `110,110,110` |
| `Sun Disc Sunset Color` | str | `255,255,255` |
| `Sun Night Color` | str | `060,060,060` |
| `Sun Sunrise Color` | str | `085,085,085` |
| `Sun Sunset Color` | str | `085,085,085` |
| `Transition Delta` | str | `.030` |
| `Wind Speed` | str | `5` |

### `[Weather Clear]` - 25 keys

| key | type | default |
|---|---|---|
| `Ambient Day Color` | str | `123,133,147` |
| `Ambient Loop Sound ID` | str | `None` |
| `Ambient Night Color` | str | `085,090,102` |
| `Ambient Sunrise Color` | str | `104,111,125` |
| `Ambient Sunset Color` | str | `123,133,147` |
| `Cloud Speed` | str | `0.3` |
| `Cloud Texture` | str | `Tx_Sky_Clear.tga` |
| `Clouds Maximum Percent` | str | `1.0` |
| `Fog Day Color` | str | `221,204,191` |
| `Fog Night Color` | str | `018,019,020` |
| `Fog Sunset Color` | str | `215,122,083` |
| `Glare View` | str | `1.00` |
| `Land Fog Day Depth` | str | `0.75` |
| `Land Fog Night Depth` | str | `0.75` |
| `Sky Day Color` | str | `115,147,189` |
| `Sky Night Color` | str | `000,000,000` |
| `Sky Sunrise Color` | str | `57,74,95` |
| `Sky Sunset Color` | str | `57,74,95` |
| `Sun Day Color` | str |  |
| `Sun Disc Sunset Color` | str | `255,255,255` |
| `Sun Night Color` | str | `069,087,135` |
| `Sun Sunrise Color` | str | `162,171,179` |
| `Sun Sunset Color` | str | `162,171,179` |
| `Transition Delta` | str | `.030` |
| `Wind Speed` | str | `5` |

### `[Weather Cloudy]` - 25 keys

| key | type | default |
|---|---|---|
| `Ambient Day Color` | str | `118,124,143` |
| `Ambient Loop Sound ID` | str | `None` |
| `Ambient Night Color` | str |  |
| `Ambient Sunrise Color` | str | `098,103,118` |
| `Ambient Sunset Color` | str | `098,103,118` |
| `Cloud Speed` | str | `1.0` |
| `Cloud Texture` | str | `Tx_Sky_Cloudy.tga` |
| `Clouds Maximum Percent` | str | `1.0` |
| `Fog Day Color` | str | `255,204,170` |
| `Fog Night Color` | str | `020,020,020` |
| `Fog Sunset Color` | str | `244,125,053` |
| `Glare View` | str | `.85` |
| `Land Fog Day Depth` | str | `0.9` |
| `Land Fog Night Depth` | str | `0.9` |
| `Sky Day Color` | str | `117,146,187` |
| `Sky Night Color` | str | `000,000,000` |
| `Sky Sunrise Color` | str | `058,073,094` |
| `Sky Sunset Color` | str | `058,073,094` |
| `Sun Day Color` | str | `255,244,221` |
| `Sun Disc Sunset Color` | str | `255,255,255` |
| `Sun Night Color` | str | `063,077,122` |
| `Sun Sunrise Color` | str | `159,160,171` |
| `Sun Sunset Color` | str | `159,160,171` |
| `Transition Delta` | str | `.030` |
| `Wind Speed` | str | `10` |

### `[Weather Foggy]` - 25 keys

| key | type | default |
|---|---|---|
| `Ambient Day Color` | str | `099,099,099` |
| `Ambient Loop Sound ID` | str | `None` |
| `Ambient Night Color` | str | `090,101,111` |
| `Ambient Sunrise Color` | str | `095,100,105` |
| `Ambient Sunset Color` | str | `095,100,105` |
| `Cloud Speed` | str | `1.2` |
| `Cloud Texture` | str | `Tx_Sky_Foggy.tga` |
| `Clouds Maximum Percent` | str | `1.0` |
| `Fog Day Color` | str | `096,103,111` |
| `Fog Night Color` | str | `038,048,048` |
| `Fog Sunset Color` | str | `099,106,114` |
| `Glare View` | str | `.30` |
| `Land Fog Day Depth` | str | `2.75` |
| `Land Fog Night Depth` | str | `2.75` |
| `Sky Day Color` | str | `096,103,111` |
| `Sky Night Color` | str | `038,048,048` |
| `Sky Sunrise Color` | str | `067,075,80` |
| `Sky Sunset Color` | str | `067,075,80` |
| `Sun Day Color` | str | `110,110,110` |
| `Sun Disc Sunset Color` | str | `255,255,255` |
| `Sun Night Color` | str | `101,114,125` |
| `Sun Sunrise Color` | str | `106,112,117` |
| `Sun Sunset Color` | str | `106,112,117` |
| `Transition Delta` | str | `.030` |
| `Wind Speed` | str | `0` |

### `[Weather Overcast]` - 25 keys

| key | type | default |
|---|---|---|
| `Ambient Day Color` | str | `099,099,099` |
| `Ambient Loop Sound ID` | str | `None` |
| `Ambient Night Color` | str | `067,067,067` |
| `Ambient Sunrise Color` | str | `083,083,083` |
| `Ambient Sunset Color` | str | `083,083,083` |
| `Cloud Speed` | str | `1.5` |
| `Cloud Texture` | str | `Tx_Sky_Overcast.tga` |
| `Clouds Maximum Percent` | str | `1.0` |
| `Fog Day Color` | str | `086,080,078` |
| `Fog Night Color` | str | `038,038,038` |
| `Fog Sunset Color` | str | `079,071,070` |
| `Glare View` | str | `0` |
| `Land Fog Day Depth` | str | `2.0` |
| `Land Fog Night Depth` | str | `2.0` |
| `Sky Day Color` | str | `086,080,078` |
| `Sky Night Color` | str | `038,038,038` |
| `Sky Sunrise Color` | str | `062,059,058` |
| `Sky Sunset Color` | str | `062,059,058` |
| `Sun Day Color` | str | `110,110,110` |
| `Sun Disc Sunset Color` | str | `255,255,255` |
| `Sun Night Color` | str | `060,060,060` |
| `Sun Sunrise Color` | str | `085,085,085` |
| `Sun Sunset Color` | str | `085,085,085` |
| `Transition Delta` | str | `.030` |
| `Wind Speed` | str | `5` |

### `[Moons]` - 23 keys

| key | type | default |
|---|---|---|
| `Masser Axis Offset` | str | `35` |
| `Masser Daily Increment` | str | `1` |
| `Masser Fade End Angle` | str | `5` |
| `Masser Fade In Finish` | str | `18` |
| `Masser Fade In Start` | str | `17` |
| `Masser Fade Out Finish` | str | `8` |
| `Masser Fade Out Start` | str | `6` |
| `Masser Fade Start Angle` | str | `10` |
| `Masser Moon Shadow Early Fade Angle` | str | `0.5` |
| `Masser Size` | str | `150` |
| `Masser Speed` | str | `1.02` |
| `Script Color` | str | `255,20,20` |
| `Secunda Axis Offset` | str | `50` |
| `Secunda Daily Increment` | str | `1.2` |
| `Secunda Fade End Angle` | str | `5` |
| `Secunda Fade In Finish` | str | `16` |
| `Secunda Fade In Start` | str | `14` |
| `Secunda Fade Out Finish` | str | `9` |
| `Secunda Fade Out Start` | str | `6` |
| `Secunda Fade Start Angle` | str | `10` |
| `Secunda Moon Shadow Early Fade Angle` | str | `0.5` |
| `Secunda Size` | str | `50` |
| `Secunda Speed` | str | `1.3` |

### `[LightAttenuation]` - 11 keys

| key | type | default |
|---|---|---|
| `ConstantValue` | str | `0.0` |
| `LinearMethod` | int |  |
| `LinearRadiusMult` | str | `1.0` |
| `LinearValue` | str | `3.0` |
| `OutQuadInLin` | int |  |
| `QuadraticMethod` | int |  |
| `QuadraticRadiusMult` | str | `1.0` |
| `QuadraticValue` | str | `16.0` |
| `UseConstant` | int |  |
| `UseLinear` | int |  |
| `UseQuadratic` | int |  |

### `[Inventory]` - 9 keys

| key | type | default |
|---|---|---|
| `DirectionalAmbientB` | str | `0.5` |
| `DirectionalAmbientG` | str | `0.5` |
| `DirectionalAmbientR` | str | `0.5` |
| `DirectionalDiffuseB` | str | `1.0` |
| `DirectionalDiffuseG` | str | `1.0` |
| `DirectionalDiffuseR` | str | `1.0` |
| `DirectionalRotationX` | str | `1.0` |
| `DirectionalRotationY` | str | `0.0` |
| `UniformScaling` | int |  |

### `[Map]` - 8 keys

| key | type | default |
|---|---|---|
| `Show Travel Lines` | int |  |
| `Travel Boat Green` | int |  |
| `Travel Boat Red` | int |  |
| `Travel Magic Green` | int |  |
| `Travel Magic Red` | int |  |
| `Travel Siltstrider Blue` | int |  |
| `Travel Siltstrider Green` | int |  |
| `Travel Siltstrider Red` | int |  |

### `[AnswerOne]` - 4 keys

| key | type | default |
|---|---|---|
| `1)` | flt |  |
| `2` | flt |  |
| `2)` | flt |  |
| `3)` | flt |  |

### `[PixelWater]` - 4 keys

| key | type | default |
|---|---|---|
| `Resolution` | int |  |
| `SecsPerEnvUpdate` | str |  |
| `SurfaceFPS` | int |  |
| `TileCount` | int |  |

### `[SaveNames]` - 4 keys

| key | type | default |
|---|---|---|
| `autosave` | flt | `autosave` |
| `census` | flt | `Census` |
| `quiksave` | flt | `Quicksave` |
| `restore` | flt | `Restore` |

### `[AnswerThree]` - 3 keys

| key | type | default |
|---|---|---|
| `1)` | flt |  |
| `2)` | flt |  |
| `3)` | flt |  |

### `[AnswerTwo]` - 3 keys

| key | type | default |
|---|---|---|
| `1)` | flt |  |
| `2)` | flt |  |
| `3` | flt |  |

### `[Debug]` - 2 keys

| key | type | default |
|---|---|---|
| `No Reboot On Load Game` | int |  |
| `No Reboot On New Game` | int |  |

### `[GeneralWarnings]` - 2 keys

| key | type | default |
|---|---|---|
| `GeneralMasterMismatchWarning` | str |  |
| `MasterMismatchWarning` | str |  |

### `[Movies]` - 2 keys

| key | type | default |
|---|---|---|
| `Morrowind Logo` | str | `mw_logo.bik` |
| `New Game` | str | `mw_intro.bik` |

### `[Xbox]` - 2 keys

| key | type | default |
|---|---|---|
| `MenuDeadZone` | str | `65` |
| `RunZone` | str | `93` |

### `[0]` - 1 keys

| key | type | default |
|---|---|---|
| `E` | str |  |

### `[Fonts]` - 1 keys

| key | type | default |
|---|---|---|
| `Palettize` | int |  |

### `[Months]` - 1 keys

| key | type | default |
|---|---|---|
| `Jan` | flt |  |

### `[PreLoad]` - 1 keys

| key | type | default |
|---|---|---|
| `Cell 0` | flt |  |
