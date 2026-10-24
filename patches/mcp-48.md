# Level-up multiplier fix

Patch key: `mcp-48`

The level-up menu can show no attribute multiplier when the earned increase would bring Strength,
Intelligence, Willpower or Agility to 100. The menu then offers only the usual one-point increase.
This patch shows the earned multiplier so a player can select it.

## How it works

The menu checks that the attribute is below 100 before offering an increase. Its left column also
rejects a multiplier when the resulting value reaches 100. The patch makes that column use the
same multiplier display path as the right column, while keeping the check on the current attribute.
It also prevents two multiplier labels from overlapping when the attribute has exactly three
skill increases and customized multiplier settings expose that case.

See the [patch table](../docs/patches.md) for availability and selection.
