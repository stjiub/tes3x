# Mod compatibility

Generated from [`catalog.toml`](../catalog.toml) by `tools/tes3x_patches.py --write`;
edit that file, not this one.

Mods tried on the Xbox build and how they fared. A scripted test only shows that a build starts and loads a few cells; only playing a mod marks it as working. The GUI's mod list shows the same verdicts.

| Status | Meaning |
|---|---|
| `works` | Confirmed working on the Xbox. |
| `works-with-requirements` | Confirmed working when its requirements are met. |
| `passes-automated` | Passed an automated test; compatibility is not yet confirmed. |
| `untested` | Compatibility has not been confirmed. |
| `broken` | Known to fail on the Xbox. |
| `not-possible` | Cannot work on the Xbox, even with patches. |

| Mod | Version | Status | Needs | Notes |
|---|---|---|---|---|
| [Delayed Dark Brotherhood Attack](https://www.nexusmods.com/morrowind/mods/14891) | unknown | `works` |  |  |
| [Graphic Herbalism](https://www.nexusmods.com/morrowind/mods/43140) | unknown | `works` |  |  |
| [Jammings Off](https://www.nexusmods.com/morrowind/mods/44523) | unknown | `works` |  |  |
| [Project Atlas](https://www.nexusmods.com/morrowind/mods/45399) | unknown | `works` |  |  |
| [Morrowind Optimization Patch](https://www.nexusmods.com/morrowind/mods/45384) | 1.17.0 | `works-with-requirements` | `dxt5-size` patch | Its DXT5 textures crash the engine on the first trip outside unless dxt5-size is on. |
| [Tamriel Data](https://www.tamriel-rebuilt.org/downloads/resources) | 26.08 | `untested` |  | Needed by Tamriel Rebuilt; see that entry. |
| [Tamriel Rebuilt](https://www.tamriel-rebuilt.org/downloads/main-release) | 26.08.23 | `untested` | `dxt5-size` patch, `heap-region` patch, `video-arena` patch, 128 MB RAM, Cerbios BIOS | Its records alone need more than 64 MB. With 128 MB its plugins load and play; with its assets too, the textures around Firewatch overflow the video memory. |
| TES Extended Music | unknown | `untested` |  |  |
| [Patch for Purists](https://www.nexusmods.com/morrowind/mods/45096) | 4.0.2 | `broken` |  | Version 4.0.2 removes Sellus Gravius' duties topic during a new game. |
