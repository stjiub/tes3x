# TES3X

TES3X is a set of tools for running modded *Morrowind Game of the Year Edition* on the
original Xbox. It includes a PC-side mod pipeline, a modular XBE patcher, and early engine
and script extensions.

The project is still young. Commands and file formats may change.

## Requirements

- Python 3.12 or newer
- [Pillow](https://pypi.org/project/pillow/) for texture conversion
- A legally obtained retail Xbox game install and `morrowind.xbe`
- For payload builds: clang and `lld-link`; set `NXDK_DIR` to an nxdk checkout

Install the Python dependency:

```powershell
python -m pip install Pillow
```

## Build a complete install

Copy `examples/local.toml` to `tes3x.local.toml` and set the clean retail game,
nxdk and Xbox paths. Keep machine-specific paths in that file and mod selection in
the profile.

```powershell
python tools/tes3x_pipeline.py examples/profile.toml --plan
python tools/tes3x_pipeline.py examples/profile.toml --dry-run
python tools/tes3x_pipeline.py examples/profile.toml --deploy
```

The pipeline resolves the mod tree, builds the selected hook payload, patches a
clean `morrowind.xbe`, packs assets, stages both XBEs, and optionally synchronizes
the complete install. Its output is replaced only when the preceding stages all
succeed. `--dry-run` builds normally but only reports the console changes.

Patch presets provide a starting policy. `standard` contains verified default
engine fixes, while `development` also enables diagnostics and the in-game
console. Profile `patches.enable` and `patches.disable` entries take precedence.
Packaging requirements such as the multi-BSA hook are selected automatically.

## Individual tools

The component commands remain available for custom builds and investigation.

### Build a mod tree

Copy `examples/profile.toml`, set `library` to a folder containing one directory per mod,
then list the mods you want to include.

```powershell
python tools/tes3x_build.py examples/profile.toml `
  --out build/tree --json build/manifest.json
```

Pack that tree with clean retail files:

```powershell
python tools/tes3x_pack.py build/tree `
  --vanilla "<clean Data Files>" --ini "<retail Morrowind.ini>" `
  --out build/deploy
```

### Patch an XBE

List the available patches, then apply only the ones you want:

```powershell
python tools/tes3x_patch.py --list
python tools/tes3x_patch.py morrowind.xbe --out patched.xbe `
  --apply drive-letters=T --apply boot-media
```

The input XBE is never modified in place. Patch matching fails if the input does not match
the expected retail code.

### Copy files back off the console

```powershell
python tools/tes3x_fetch.py "E:/UDATA/42530005" --host 192.0.2.10 --out build/saves
python tools/tes3x_fetch.py "E:/" --host 192.0.2.10 --list
```

Takes a file, a directory or a wildcard. Directories are copied whole. `T:` and `U:` are mapped
only while a title runs and the FTP server refuses them; their contents live under
`E:/TDATA/<title id>` and `E:/UDATA/<title id>`.

More pipeline options are recorded in [docs/pipeline.md](docs/pipeline.md). The MCP porting
work is tracked in [docs/mcp-port.md](docs/mcp-port.md).
