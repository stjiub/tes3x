"""Talk to the payload's network driver (the `multiplayer` patch).

    tes3x net listen                     # console broadcasts on UDP 26500
    tes3x net ping 192.0.2.50             # echo round trips to `tes3xnet up`
    tes3x net listen --tunnel 9369       # the same through xemu's udp backend
    tes3x net ping 10.0.2.15 --tunnel 9369
    tes3x net serve --tunnel 9369 --bot   # plus a player circling the first client
    tes3x net serve --tunnel 9369 --bot --bot-say 2 --drop 0.2   # events under loss
    tes3x net serve --tunnel 9369 --bot --bot-owns 20:60   # the bot runs the cell
    tes3x net serve --tunnel 9369 --tunnel 9371   # two xemus, one per tunnel
    tes3x net serve --tunnel 9369 --send FILE   # to TES3X on each console's U:
    tes3x net plugin OUT.esp --master Morrowind.esm   # the ghost plugin

With --tunnel PORT this tool is the guest's only peer: xemu sends each guest Ethernet frame to
PORT as one datagram and accepts frames on PORT+1 (`tes3x xemu --net-tunnel PORT`), so ping
answers ARP itself and resolves the console's MAC before it pings."""

from types import ModuleType as _Module

from tes3x.paths import resource  # noqa: F401
from .proto import *  # noqa: F403
from .access import *  # noqa: F403
from .admin import *  # noqa: F403
from .bot import *  # noqa: F403
from .server import *  # noqa: F403
from .state import *  # noqa: F403
from .stream import *  # noqa: F403
from .transport import *  # noqa: F403
from .tunnel import *  # noqa: F403
from .cli import *  # noqa: F403
from .plugin import *  # noqa: F403

__all__ = sorted(name for name, value in list(globals().items())
                 if not name.startswith("_") and not isinstance(value, _Module))
