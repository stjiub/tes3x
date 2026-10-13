"""TES3X: tools for the original Xbox Morrowind."""

__version__ = "0.1.0"


def _hide_child_consoles():
    """Without a console of our own (pythonw, the GUI), console programs we start would each
    open a window; create them without one."""
    import subprocess
    import sys
    if sys.platform != "win32":
        return
    import ctypes
    if ctypes.windll.kernel32.GetConsoleWindow():
        return
    original = subprocess.Popen.__init__

    def init(self, *args, **kwargs):
        kwargs["creationflags"] = kwargs.get("creationflags", 0) | subprocess.CREATE_NO_WINDOW
        if len(args) < 4:
            # a hidden console would swallow what the child writes: hand on our own streams
            for name, stream in (("stdin", sys.stdin), ("stdout", sys.stdout),
                                 ("stderr", sys.stderr)):
                if kwargs.get(name) is None:
                    try:
                        stream.fileno()
                    except (AttributeError, OSError, ValueError):
                        stream = subprocess.DEVNULL
                    kwargs[name] = stream
        original(self, *args, **kwargs)

    subprocess.Popen.__init__ = init


_hide_child_consoles()
