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

The game test's `tes3xmcp3` command calls the same virtual armor-rating and damage functions with
fixed 50-point inputs. It verifies that no armor is equipped and compares the returned damage in
control and patched builds. This measures the corrected calculation without applying damage to
health; a real combat hit remains an end-to-end smoke test.
