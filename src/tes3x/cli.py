"""The tes3x command: `tes3x COMMAND [ARGS]` runs one of TES3X's tools.

`tes3x --help` lists the commands; `tes3x COMMAND --help` gives a command's own options.
"""
import ast
import importlib.util
import runpy
import sys

from tes3x import __version__

# The modules with a command line, run as `python -m tes3x.NAME` would run them. On the command
# line a hyphen may stand for an underscore: `tes3x xemu-setup`.
COMMANDS = ("agent", "assets", "audit", "bsa", "build", "convert", "deploy", "diag", "disasm",
            "docs", "ess", "fatx", "fetch", "font", "gui", "heap", "ini", "init", "inject", "layouts",
            "library", "manager", "manifest", "map", "mcp", "mem", "menuart", "mwse", "net",
            "nexus", "nxdk", "optimize", "orphan", "pack", "package", "patch",
            "patches", "payload", "pipeline", "plugins", "prof", "put", "qcow2", "readlog",
            "release", "saves", "scenario", "scriptasm", "sym", "test", "tour", "validate", "xbe",
            "xemu", "xemu_setup")


def summary(name):
    """The first sentence of a command's module docstring, read without importing the module."""
    with open(importlib.util.find_spec(f"tes3x.{name}").origin, encoding="utf-8") as stream:
        doc = ast.get_docstring(ast.parse(stream.read())) or ""
    return " ".join(doc.split("\n\n")[0].split()).split(". ")[0].rstrip(".")


def usage(stream):
    width = max(map(len, COMMANDS)) + 2
    print("usage: tes3x COMMAND [ARGS]\n\nRun `tes3x COMMAND --help` for a command's options.\n\n"
          "commands:", file=stream)
    for name in COMMANDS:
        print(f"  {name.replace('_', '-'):{width}}{summary(name)}", file=stream)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else list(argv)
    if not argv or argv[0] in ("-h", "--help", "help"):
        usage(sys.stdout)
        return 0
    if argv[0] == "--version":
        print(f"tes3x {__version__}")
        return 0
    name = argv[0].replace("-", "_")
    if name not in COMMANDS:
        print(f"tes3x: no command {argv[0]!r}\n", file=sys.stderr)
        usage(sys.stderr)
        return 2
    # The command's argparse names itself after argv[0]: "usage: tes3x pipeline ..."
    sys.argv = [f"tes3x {name.replace('_', '-')}", *argv[1:]]
    runpy.run_module(f"tes3x.{name}", run_name="__main__", alter_sys=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
