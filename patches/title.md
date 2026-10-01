# Dashboard title

Patch key: `title=NAME`

Several TES3X builds can be installed side by side, and a dashboard would list them all as
"Morrowind". This patch renames the XBE's certificate title, so each build has its own name.

See the [patch table](../docs/patches.md) for availability and selection.

## How it works

The certificate holds the title as 40 UTF-16 characters (certificate offset `0x190`). The patch
writes the new name there, padded with zeros; a longer name is refused. The pipeline renames both
`Default.xbe` and `morrowind.xbe`, since a dashboard lists the launcher.

## Configuration

A profile's `profile.title`, or the pipeline's `--title`; directly, `--apply title=NAME`.

## Compatibility and limits

Some dashboards take the name from their own metadata before the certificate, and cache what they
read when they scan. With a title set, the pipeline also writes the metadata for the dashboards a
profile lists in `dashboards`; see [configuration](../docs/configuration.md).
