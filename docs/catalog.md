# Mod compatibility

Generated from [`catalog.toml`](../catalog.toml) by `tes3x patches --write`;
edit that file, not this one.

Mods tried on the Xbox build and how they fared. A scripted test only shows that a build starts and loads a few cells; only playing a mod marks it as working. Memory is the most free memory the mod cost against vanilla on the memory tour. The GUI's mod list shows the same verdicts.

| Status | Meaning |
|---|---|
| `works` | Confirmed working on the Xbox. |
| `works-with-requirements` | Confirmed working when its requirements are met. |
| `works-partially` | Confirmed working with some of its content left out; see its exclude list and notes. |
| `passes-automated` | Passed an automated test; compatibility is not yet confirmed. |
| `untested` | Compatibility has not been confirmed. |
| `broken` | Known to fail on the Xbox. |
| `not-possible` | Cannot work on the Xbox, even with patches. |

| Mod | Version | Status | Needs | Memory | Notes |
|---|---|---|---|---|---|
| Better_Clothes | unknown | `works` |  |  |  |
| BetterBodies | unknown | `works` |  |  |  |
| BetterHeads | unknown | `works` |  |  |  |
| [Delayed Dark Brotherhood Attack](https://www.nexusmods.com/morrowind/mods/14891) | unknown | `works` |  |  |  |
| [Graphic Herbalism](https://www.nexusmods.com/morrowind/mods/43140) | unknown | `works` |  |  |  |
| [Jammings Off](https://www.nexusmods.com/morrowind/mods/44523) | unknown | `works` |  |  |  |
| MoreBetterClothes | unknown | `works` |  |  |  |
| [Project Atlas](https://www.nexusmods.com/morrowind/mods/45399) | unknown | `works` |  |  |  |
| RealSignposts | unknown | `works` |  |  |  |
| [Morrowind Optimization Patch](https://www.nexusmods.com/morrowind/mods/45384) | 1.17.0 | `works-with-requirements` | `dxt5-size` patch |  | Its DXT5 textures crash the engine on the first trip outside unless dxt5-size is on. |
| [Patch for Purists](https://www.nexusmods.com/morrowind/mods/45096) | 4.0.2 | `passes-automated` | `dialogue-merge` patch, `mcp-114` patch, `mcp-119` patch |  | The Xbox loader drops stock responses when this mod repeats a dialogue topic; dialogue-merge keeps them. Separate axe inventory sounds and creature voiceovers need the two MCP compatibility patches. Its xbase_anim.kf and xbase_anim_female.kf freeze New Game because their root rotation is XYZ Euler, which hangs the Xbox loader, and they replace retail's Xbox-specific animations. The pipeline replaces them with retail's KFs plus the mod's idle-clip root translation, which is its only animation fix. |
| Can't Touch This | 1.0.3 | `untested` | `cant-touch-this` patch |  | Dodge animations on missed melee attacks. Its OpenMW Lua is not used; the patch plays the animations. |
| [Tamriel Data](https://www.tamriel-rebuilt.org/downloads/resources) | 26.08 | `untested` |  |  | Needed by Tamriel Rebuilt; see that entry. |
| [Tamriel Rebuilt](https://www.tamriel-rebuilt.org/downloads/main-release) | 26.08.23 | `untested` | `dxt5-size` patch, `heap-region` patch, `video-arena` patch, 128 MB RAM, Cerbios BIOS |  | Its records alone need more than 64 MB. With 128 MB its plugins load and play; with its assets too, the textures around Firewatch overflow the video memory. |
| TES Extended Music | unknown | `untested` |  |  |  |
