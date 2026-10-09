# Plugin optimizer

The plugin optimizer writes derived copies of a load order that produce the same modeled Xbox
engine state while making the loader construct fewer records.

It never changes a source plugin. The output directory must not already exist, every copy keeps
the source filename, and the tool updates the `HEDR` record count and dependent `MAST`/`DATA`
sizes. It then replays the original and derived load orders and refuses the result unless their
state digests match.

## Optimizing a load order

Pass every master and plugin in the order the Xbox loads them:

```
tes3x optimize --output build/optimized Morrowind.esm Mod.esm Patch.esp
```

The output contains the derived plugins and `tes3x-optimize.json`, which records the model,
input and output hashes, applied passes, removal counts and final state digest. With no `--output`,
the command only reports what its passes would remove.

The first implemented pass removes earlier `SCPT` definitions that a later script completely
replaces. Flagged or deleted definitions stay until their inherited state is modeled fully.
Record types without an implemented engine rule are copied byte for byte and included in the
equivalence digest as an unchanged stream.

## Checking existing copies

Use the same original load order and name the directory containing derived copies:

```
tes3x optimize --check build/optimized Morrowind.esm Mod.esm Patch.esp
```

The current model proves equivalence only for implemented transformations. Removing definitions
because no static reference reaches them is a separate, closed-world operation and is not yet part
of this command.
