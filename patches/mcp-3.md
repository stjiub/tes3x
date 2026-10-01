# Unarmored damage reduction

The physical-damage path asks the actor how many armor pieces are equipped. Retail skips its
complete damage-reduction calculation when that count is zero, even though the calculation handles
unarmored body parts. A fully unarmored actor therefore receives no protection from the Unarmored
skill; equipping any one armor piece makes the skill contribute again. This patch lets the
calculation run for a fully unarmored actor.

See the [patch table](../docs/patches.md) for availability and selection.

## How it works

The Xbox function at `0x00176700` has the same defect as the PC build. Its branch at `0x00176759`
skips to `0x001769BA` when the equipped-armor count is zero. The patch makes that branch always
continue at `0x0017675F`, allowing the existing armor and Unarmored calculation to run. It changes
six bytes and needs no injected code.

## Compatibility and limits

Fully unarmored characters take less physical damage than in the retail game, which changes the
balance for characters built around the skill.
