# mcp-140: loading screen redraw throttle

Port of Morrowind Code Patch fix 140, improved loading speed.

The loading screen's status and progress callbacks redraw far more often than needed, and each
redraw waits for the GPU. The Xbox `MenuLoading` callbacks keep all three costs MCP removed on PC.

## What the patch changes

- `0x001E949D`: skip the redundant status update.
- `0x001E94C6`: change that redraw's mode from 1 to 0.
- `0x001E923F`: throttle the progress update and redraw to one per 50 ms, timed with
  `KeQuerySystemTime` (`hooks/tes3xmcp140.c`).

## What remains

It is a performance change, so it stays out of the presets until an original-hardware comparison
shows a loading-time win. xemu timings do not count.
