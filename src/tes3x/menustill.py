"""The main menu still: one frame of the retail logo movie, as raw 640x480 BGRA."""

import shutil
import subprocess
from pathlib import Path

VIDEO = "mw_logo.bik"
FRAME_SECONDS = 3.5  # the emblem is up and no text has appeared
WIDTH, HEIGHT = 640, 480
NAME = "tes3x_menu_still.raw"


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
         "-frames:v", "1", "-vf", f"scale={WIDTH}:{HEIGHT}", "-f", "rawvideo", "-pix_fmt", "bgra",
         str(out)], capture_output=True, text=True)
    if run.returncode or not out.is_file() or out.stat().st_size != WIDTH * HEIGHT * 4:
        out.unlink(missing_ok=True)
        print(f"  menu still: ffmpeg could not read {Path(video).name}: {run.stderr.strip()}")
        return False
    print(f"  menu still: {NAME} from {Path(video).name} at {FRAME_SECONDS} s")
    return True
