# Mod compatibility

Generated from [`catalog.toml`](../catalog.toml) by `tools/tes3x_patches.py --write`;
edit that file, not this one.

Mods tried on the Xbox build and how they fared. A scripted test only shows that a build starts and loads a few cells; only playing a mod marks it as working. The GUI's mod list shows the same verdicts.

| Status | Meaning |
|---|---|
| `works` | Played on the Xbox build without problems. |
| `works-with-requirements` | Works once its requirements are met. |
| `passes-automated` | Passed an automated smoke test; not yet played. |
| `investigating` | Being tried; the notes say how far. |
| `untested` | Not tried yet. |
| `broken` | Fails on the Xbox build; the notes say how. |
| `not-possible` | Cannot work on the Xbox, even with patches. |

| Mod | Version | Status | Needs | Notes |
|---|---|---|---|---|
| [Delayed Dark Brotherhood Attack](https://www.nexusmods.com/morrowind/mods/14891) | unknown | `works` |  | Played from Seyda Neen to Balmora alongside other mods, not on its own. |
| [Graphic Herbalism](https://www.nexusmods.com/morrowind/mods/43140) | unknown | `works` |  | Played from Seyda Neen to Balmora alongside other mods, not on its own. |
| [Jammings Off](https://www.nexusmods.com/morrowind/mods/44523) | unknown | `works` |  | Played from Seyda Neen to Balmora alongside other mods, not on its own. |
| [Project Atlas](https://www.nexusmods.com/morrowind/mods/45399) | unknown | `works` |  | Played from Seyda Neen to Balmora alongside other mods, not on its own. |
| [Morrowind Optimization Patch](https://www.nexusmods.com/morrowind/mods/45384) | 1.17.0 | `works-with-requirements` | `dxt5-size` patch | Its DXT5 textures crash the engine on the first trip outside unless dxt5-size is on. |
| [Patch for Purists](https://www.nexusmods.com/morrowind/mods/45096) | 4.0.2 | `investigating` |  | Only the intro and Seyda Neen have been played. Newer versions have not been tried. |
| [Tamriel Data](https://www.tamriel-rebuilt.org/downloads/resources) | 26.08 | `investigating` |  | Needed by Tamriel Rebuilt; see that entry. |
| [Tamriel Rebuilt](https://www.tamriel-rebuilt.org/downloads/main-release) | 26.08.23 | `investigating` | `dxt5-size` patch, `heap-region` patch, `video-arena` patch, 128 MB RAM, Cerbios BIOS | Its records alone need more than 64 MB. With 128 MB its plugins load and play; with its assets too, the textures around Firewatch overflow the video memory. |
| TES Extended Music | unknown | `investigating` |  | Only the intro and Seyda Neen have been played. |
