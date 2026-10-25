"""The main menu still: the first frame of the retail menu loop, as raw 640x480 BGRA."""

import shutil
import subprocess
from pathlib import Path

VIDEO = "mw_menu.bik"
FRAME_SECONDS = 0.0  # the title is up and the runes have not yet faded in
WIDTH, HEIGHT = 640, 480
NAME = "tes3x_menu_still.raw"
# One decoded frame is blocky; the first second averaged is clean, then lightly denoised
AVERAGE = "select='lt(n,30)',tmix=frames=30,select='eq(n,29)',hqdn3d=4:4:0:0,"
# The title moves up 60 pixels so it clears the menu's buttons: the top is cropped, and the empty
# parchment below the title is stretched to fill
FILTER = (AVERAGE + "scale=640:480,split[a][b];[a]crop=640:240:0:60[t];"
          "[b]crop=640:180:0:300,scale=640:240[u];[t][u]vstack")


def make_still(video, out):
    """Write the still to `out`; return False, with a reason printed, when it cannot be made."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        print("  menu still: ffmpeg not found; the logo movie plays instead")
        return False
    if not Path(video).is_file():
        print(f"  menu still: {video} not found; the logo movie plays instead")
        return False
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists():
        out.unlink()  # a hard link must not be written through
    run = subprocess.run(
        [ffmpeg, "-v", "error", "-y", "-ss", str(FRAME_SECONDS), "-i", str(video),
         "-frames:v", "1", "-fps_mode", "passthrough", "-vf", FILTER, "-f", "rawvideo", "-pix_fmt", "bgra",
         str(out)], capture_output=True, text=True)
    if run.returncode or not out.is_file() or out.stat().st_size != WIDTH * HEIGHT * 4:
        out.unlink(missing_ok=True)
        print(f"  menu still: ffmpeg could not read {Path(video).name}: {run.stderr.strip()}")
        return False
    print(f"  menu still: {NAME} from {Path(video).name} at {FRAME_SECONDS} s")
    return True
