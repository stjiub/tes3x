# Unarmored fix

See the [patch table](../docs/patches.md) for status and selection details.

The physical-damage path asks the actor how many armor pieces are equipped. Retail skips its
complete damage-reduction calculation when that count is zero, even though the calculation handles
unarmored body parts. A fully unarmored actor therefore receives no protection from the Unarmored
skill; equipping any one armor piece makes the skill contribute again.

## What the patch changes

The Xbox function at `0x00176700` has the same defect as the PC build. Its branch at `0x00176759`
skips to `0x001769BA` when the equipped-armor count is zero. The patch makes that branch always
continue at `0x0017675F`, allowing the existing armor and Unarmored calculation to run. It changes
six bytes and needs no injected code.

## What remains

The equivalent Xbox function and edit are established statically. A control-versus-patched combat
test with a fully unarmored actor still needs to measure health lost from the same physical hit.
