# Configuration reference

A profile says what to build. `tes3x.local.toml` holds the settings for your computer: where the
retail game, mods and tools are, and how to reach the Xbox.

`python tools/tes3x_pipeline.py profiles/my-build.toml --check` checks both files and prints
what would be built.

## Local config

The default is `tes3x.local.toml` in the current directory; the GUI falls back to the
one in the TES3X folder. `--config PATH` picks another. Relative paths are relative to the file.

### `[paths]`

| Key | Type | Default | Meaning |
|---|---|---|---|
| `vanilla_root` | string | none | Clean retail game folder containing both XBEs, `Morrowind.ini` and `Data Files`. Required to build unless `--vanilla` is used. |
| `mod_library` | string | none | Mod library root. A profile's `library` takes precedence. |
| `profiles` | string | `profiles` | Folder the GUI lists profiles from. |
| `mlox_rules` | string | none | mlox's `mlox_base.txt`, for `rules.plugin_order = "mlox"`. |
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
| `library` | string | local config | Directory containing mod folders and optional `library.toml`. |

### `[patches]`

| Key | Type | Default | Meaning |
|---|---|---|---|
| `preset` | string | `standard` | `minimal`, `standard` or `development`. |
| `categories` | array of strings | `[]` | Add selectable patches by category. Categories are `core`, `correctness`, `compat`, `performance`, `qol`, `balance`, `instrumentation` and `infrastructure`. |
| `enable` | array of strings | `[]` | Add individual patches by name. |
| `disable` | array of strings | `[]` | Remove individual patches selected elsewhere. |

`minimal` adds no optional patches. `standard` adds the tested core and correctness fixes and the
in-game console. `development` adds the untested ones and diagnostics. See
[patches.md](patches.md) for patch names.

### `[preferences]`

Applied after the Xbox's stored options load. Leave the table out to keep the player's own
settings.

| Key | Type | Default | Meaning |
|---|---|---|---|
| `invert_look` | boolean | stored/retail value | Set right-stick vertical look to inverted or normal. |

### `[package]`

The archive settings matter only when the profile has enabled mods. `drive_letter` applies to
every build.

| Key | Type | Default | Meaning |
|---|---|---|---|
| `mode` | string | `delta-bsa` | `delta-bsa` puts mod files in their own archive; `merged-bsa` rebuilds `Morrowind.bsa` with them; `loose` ships them loose. |
| `archive_name` | string | `tes3xmods.bsa` | Archive name in `delta-bsa` mode. |
| `archive_only` | boolean | `false` | Set `TryArchiveFirst=1` and skip loose asset lookups. Not with `loose`. |
| `drive_letter` | string | `D` | Drive used for game-directory asset paths. |
| `loose_assets` | array of strings | `[]` | Path patterns kept loose in an archive build. |

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
| `plugin_order` | string | `mods` | `mods` loads plugins in mod order, or in `[plugins] order` when set; `mlox` sorts them with mlox. See [pipeline options](pipeline.md#sorting-plugins-with-mlox). |

### `[plugins]`

| Key | Type | Default | Meaning |
|---|---|---|---|
| `order` | array of strings | mod order | Plugin load order. Masters still load before other plugins and before plugins that need them; plugins not listed follow in mod order. Cannot be combined with `plugin_order = "mlox"`. |

### `[[mods]]`

Add one table per mod. Remove all mod tables and `profile.library` for a patches-only build.

| Key | Type | Default | Meaning |
|---|---|---|---|
| `name` | string | | A folder in the mod library; the whole folder is used. |
| `id` | string | | A mod's id in the library's `library.toml`. |
| `version` | string | library default | Release to use, for a mod picked by id. |
| `components` | array of strings | component defaults | Optional installer folders to add, for a mod picked by id. |
| `order` | integer | `0` | Conflict priority; higher values win. |
| `enabled` | boolean | `true` | Include this mod. |
| `optional` | boolean | `false` | Skip the mod if its folder is absent. |
| `plugins` | array of strings | all | Include only these plugins from the mod folder. |
| `loose` | boolean | `false` | Ship this mod's winning assets loose instead of archiving them. |
| `archives` | string | `unpack` | `unpack` adds the files in the mod's `.bsa` archives to the build, beneath its loose files. `load` ships the archives and has the `multi-bsa` patch open them; archives that store no file names can only be loaded. |

Every entry needs exactly one of `name` or `id`. See [the mod library](mod-library.md).

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
