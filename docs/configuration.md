# Configuration reference

TES3X reads a build profile and an optional local config. The profile describes what to build;
`tes3x.local.toml` holds retail, tool, output and Xbox connection paths for one computer.

Run `python tools/tes3x_pipeline.py profile.toml --check` after editing either file. It validates
the supported keys and prints the resolved build without reading the retail game files.

## Local config

The default path is `tes3x.local.toml` in the current directory. Use `--config PATH` to select
another file. Relative paths are resolved from the local config's directory.

### `[paths]`

| Key | Type | Default | Meaning |
|---|---|---|---|
| `vanilla_root` | string | none | Clean retail game folder containing both XBEs, `Morrowind.ini` and `Data Files`. Required to build unless `--vanilla` is used. |
| `build_root` | string | `build` | Parent directory for profile output folders. |
| `llvm` | string | `PATH` | Directory containing `clang` and `lld-link`. |
| `hardlink_retail` | boolean | `false` | Hardlink unchanged retail files when source and output are on the same volume. |

### `[deploy]`

| Key | Type | Default | Meaning |
|---|---|---|---|
| `host` | string | none | Xbox address. Required for `--dry-run` and `--deploy`. |
| `port` | integer | `21` | FTP port. |
| `user` | string | `xbox` | FTP user. |
| `password` | string | `xbox` | FTP password. `TES3X_FTP_PASSWORD`, `--password` and `--ask-password` take precedence. |
| `remote_root` | string | none | Destination game folder. A profile's `remote_root` takes precedence. |

## Profile

The annotated [example profile](../examples/profile.toml) is the shortest starting point.

### `[profile]`

| Key | Type | Default | Meaning |
|---|---|---|---|
| `name` | string | required | Build name and output directory name. |
| `title` | string | retail title | Dashboard title written to both XBEs and selected dashboard metadata. |
| `dashboards` | array of strings | `["xbmc4gamers"]` | Dashboard metadata formats to write when `title` is set. Supported: `xbmc4gamers`; use `[]` for none. |
| `remote_root` | string | local config | Destination game folder for this build. |
| `library` | string | none | Directory containing one folder per mod. Required when a mod is enabled. |

### `[patches]`

| Key | Type | Default | Meaning |
|---|---|---|---|
| `preset` | string | `standard` | `minimal`, `standard` or `development`. |
| `categories` | array of strings | `[]` | Add selectable patches by category. Categories are `core`, `correctness`, `compat`, `performance`, `qol`, `balance`, `instrumentation` and `infrastructure`. |
| `enable` | array of strings | `[]` | Add individual patches by name. |
| `disable` | array of strings | `[]` | Remove individual patches selected elsewhere. |

`minimal` adds no optional engine fixes. `standard` selects verified core and correctness fixes.
`development` adds instrumentation and the in-game console. Use
`python tools/tes3x_patch.py --list` or see [patches.md](patches.md) for patch names.

### `[preferences]`

Build preferences are applied after the Xbox's stored player options load. Omit this table to
leave existing preferences and retail defaults unchanged.

| Key | Type | Default | Meaning |
|---|---|---|---|
| `invert_look` | boolean | stored/retail value | Set right-stick vertical look to inverted or normal. |

Enable `transition-autosaves` in `[patches]` to save before activated doors, scripted teleports,
intervention-style magic and paid travel. It deliberately does not save for ordinary exterior
cell streaming or while a saved game is being restored. `[Xbox] TransitionAutosaves=0` disables
the added triggers at runtime. Pair it with `rotating-autosaves` to spread these saves over the
configured slot count.

### `[package]`

The archive settings matter only when the profile has enabled mods. `drive_letter` applies to
every build.

| Key | Type | Default | Meaning |
|---|---|---|---|
| `mode` | string | `delta-bsa` | `delta-bsa` keeps retail `Morrowind.bsa`; `merged-bsa` rebuilds it with mod assets. |
| `archive_name` | string | `tes3xmods.bsa` | Archive name in `delta-bsa` mode. |
| `archive_only` | boolean | `false` | Set `TryArchiveFirst=1` and skip loose asset lookups. |
| `drive_letter` | string | `D` | Drive used for game-directory asset paths. |
| `loose_assets` | array of strings | `[]` | Path patterns that must remain loose. |

`delta-bsa` and engine patches require LLVM. A patches-only profile does not package `Data Files`;
it stages the retail directory unchanged.

### `[rules]`

| Key | Type | Default | Meaning |
|---|---|---|---|
| `max_texture_size` | integer | `512` | Downscale mod textures whose longest side exceeds this value. |
| `max_filename` | integer | `42` | Maximum filename component length. Cannot exceed the FATX limit of 42. |
| `convert_all_textures` | boolean | `false` | Convert every mod texture, not only oversized textures. |
| `exclude` | array of strings | built-in list | Replace the default patterns excluded from mod folders. |
| `keep_assets` | array of strings | `[]` | Preserve matching assets during reachability pruning. |
| `clear_cache_partitions` | boolean | `false` | Clear the Xbox X/Y/Z cache after a successful deployment. |

### `[[mods]]`

Add one table per mod. Remove all mod tables and `profile.library` for a patches-only build.

| Key | Type | Default | Meaning |
|---|---|---|---|
| `name` | string | required | Folder or file path relative to `profile.library`. |
| `order` | integer | `0` | Conflict priority; higher values win. |
| `enabled` | boolean | `true` | Include this mod. |
| `optional` | boolean | `false` | Skip the mod if its folder is absent. |
| `plugins` | array of strings | all | Include only these plugins from the mod folder. |
| `loose` | boolean | `false` | Ship this mod's winning assets loose instead of archiving them. |

### `[ini]`

Each entry sets one `Morrowind.ini` value. Keys use `Section:Key` syntax.

```toml
[ini]
"General:Show FPS" = 1
"Xbox:Diagnostics" = 1
```

## Command-line precedence

Command-line values override the profile and local config. The main overrides are `--preset`,
`--enable`, `--disable`, `--package-mode`, `--drive`, `--title`, `--ini-set`, `--vanilla`,
`--llvm`, `--build-root`, `--out` and `--hardlink`. Run
`python tools/tes3x_pipeline.py --help` for their exact syntax.
