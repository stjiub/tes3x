"""Optional add-ons. `[addons]` in tes3x.local.toml switches each on by name, and the GUI loads
only those. Each add-on is a package here providing:

    LABEL        its name in Settings
    DESCRIPTION  one sentence for Settings
    status(local)
                 None when it can run, otherwise why not; `local` is the parsed local config
    PLAY         {key: (menu label, button suffix)}: the targets it adds to the Play button
    play_steps(key, context)
                 [(script, arguments, message)] to run in order for a Play target; context has
                 profile, config, deploy (the built tree), plain (the parsed profile) and local

and optionally:

    confirm(key, context)
                 a question the GUI asks before a profile's first play on that target
    SETTINGS_ACTIONS
                 [(button label, script, arguments)] run from Settings, after saving it
"""

import importlib

NAMES = ("console",)


def enabled(local):
    return [name for name in NAMES if local.get("addons", {}).get(name)]


def load(name):
    return importlib.import_module(f"addons.{name}")
