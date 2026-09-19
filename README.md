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

## Build a mod tree

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

## Patch an XBE

List the available patches, then apply only the ones you want:

```powershell
python tools/tes3x_patch.py --list
python tools/tes3x_patch.py morrowind.xbe --out patched.xbe `
  --apply drive-letters=T --apply boot-media
```

The input XBE is never modified in place. Patch matching fails if the input does not match
the expected retail code.

More pipeline options are recorded in [docs/pipeline.md](docs/pipeline.md). The MCP porting
work is tracked in [docs/mcp-port.md](docs/mcp-port.md).
