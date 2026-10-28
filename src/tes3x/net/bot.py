"""The test bot that plays a player in a session."""

import math
import struct
import time
from .proto import (ACTOR, ACTOR_IN_COMBAT, ACTOR_PERIOD, ACTORS, ACTORS_PER_PACKET, BOT_ID,
                    BUSY_SAVING, describe_state, describe_status, ENTRY_DATA, EVENT_AFFECT,
                    EVENT_BOUNTY, EVENT_BUSY, EVENT_CAST, EVENT_DEATH, EVENT_HIT, EVENT_HOLD,
                    EVENT_HOLD_BROKEN, EVENT_PLAYER, EVENT_PLAYER_HIT, EVENT_SHOT, EVENT_SPELL,
                    EVENT_STATUS, EVENT_TEXT, IN_WORLD, NO_ANIM, pack_equipment, pack_identity,
                    PEER, PLACE, PLAYER_ALIVE, PLAYER_DEATH, SHOT, SOURCE_SPELL, SPELL, STANCE,
                    STATE_BODY, STATUS, zstr)


class Bot:
    """A scripted player for tests (--bot and the --bot-* options). It circles where the
    first client entered the world and acts on a schedule counted from when it first
    placed itself."""

    def __init__(self, server, now):
        self.server, self.args = server, server.args
        self.start = now
        self.anchor = None  # ((flags, cell), x, y, z) of the client it circles
        self.anchored = None  # when it first placed itself; the schedule's zero
        self.state = None
        self.next_step = self.said = 0.0
        self.line = self.held = 0
        self.breaks = []  # (due, holder, refid) of holds it breaks
        self.hit = self.killed = self.hit_player = self.busy = self.dead = False
        self.away = False
        self.mirror = self.echo = None
        self.actor_next = 0.0
        if self.args.bot:
            self.server.identities[BOT_ID] = pack_identity("Bot", "Imperial",
                                                           "b_n_imperial_m_head_01",
                                                           "b_n_imperial_m_hair_01")
        if self.args.bot_equip is not None:
            self.server.equipment[BOT_ID] = [
                pack_equipment([i for i in self.args.bot_equip.split(",") if i]), []]
        self.boxes = []
        for spec in self.args.bot_contents:
            what, _, at = spec.rpartition("@")
            refid, cell, items = what.split(":", 2)
            entries = []
            for item in items.split(","):
                name, count, *condition = item.split("*")
                entries.append([name, int(count), ENTRY_DATA if condition else 0,
                                int(condition[0]) if condition else 0, 0])
            self.boxes.append((float(at), int(refid, 16), int(cell), entries))
        self.spawns = []
        for spec in self.args.bot_spawn:
            what, _, at = spec.rpartition("@")
            name, cell, *condition = what.split(":")
            self.spawns.append((float(at), name, int(cell),
                                int(condition[0]) if condition else None))
        self.takes = [float(at) for at in self.args.bot_take]
        self.fights = {int(refid, 16): int(client) for refid, _, client in
                       (spec.partition(":") for spec in self.args.bot_fights)}
        self.weather = []
        self.statuses = list(self.args.bot_status)
        self.affects = list(self.args.bot_affect)
        self.spells = []
        self.bounties = [(float(at), int(value)) for value, _, at in
                         (s.rpartition("@") for s in self.args.bot_bounty)]
        self.journal = []
        for spec in self.args.bot_journal:
            what, _, at = spec.rpartition("@")
            quest, _, index = what.rpartition(":")
            self.journal.append((float(at), quest, int(index)))
        self.followers = [(float(at), int(refid, 16))
                          for refid, _, at in (s.rpartition("@") for s in self.args.bot_follower)]
        self.shots = [(float(at), ammo)
                      for ammo, _, at in (s.rpartition("@") for s in self.args.bot_shoot)]
        for kind, specs in ((EVENT_SPELL, self.args.bot_spell), (EVENT_CAST, self.args.bot_cast)):
            for spec in specs:
                cast, _, at = spec.partition("@")
                name, _, refid = cast.partition(":")
                target = None if refid == "none" else int(refid, 16) if refid else 0
                self.spells.append((float(at), kind, name, target))
        for spec in self.args.bot_weather:
            change, _, at = spec.partition("@")
            index, _, value = change.partition(":")
            self.weather.append((float(at), int(index), int(value)))

    def window(self, spec, now):
        """Whether now falls in START:END seconds after the bot first placed itself."""
        if not spec or self.anchored is None:
            return False
        start, _, end = spec.partition(":")
        t = now - self.anchored
        return float(start) <= t < (float(end) if end else math.inf)

    def follow(self, state):
        """The bot circles where the first client entered the world, and follows it to a new
        cell or across a long jump."""
        flags, x, y, z, _, cell = STATE_BODY.unpack_from(state)
        flags &= PLACE
        anchor = self.anchor
        if flags & IN_WORLD and (anchor is None or anchor[0] != (flags, cell)
                                 or math.hypot(x - anchor[1], y - anchor[2]) > 2048):
            self.anchor = ((flags, cell), x, y, z)
            if self.anchored is None:
                self.anchored = time.monotonic()
            print(f"{time.strftime('%H:%M:%S')} bot circles {describe_state(state)}",
                  flush=True)

    def step(self, now):
        (flags, cell), cx, cy, cz = self.anchor
        if self.args.bot_at:
            dx, dy = (float(v) for v in self.args.bot_at.split(","))
            cx, cy = cx + dx, cy + dy
        t = (now - self.start) * 2 * math.pi / self.args.bot_period
        if self.mirror:
            _, x, y, z, heading, _, actor_flags, _, _, _, anim = ACTOR.unpack(self.mirror)
            state = STATE_BODY.pack(flags | actor_flags & STANCE, x + self.args.bot_shift, y, z,
                                    heading, cell) + anim
        elif self.echo:
            echo_flags, x, y, z, heading, echo_cell = STATE_BODY.unpack_from(self.echo)
            state = STATE_BODY.pack(echo_flags, x + self.args.bot_shift, y, z, heading,
                                    echo_cell) + self.echo[STATE_BODY.size:]
        else:
            state = STATE_BODY.pack(flags, cx + self.args.bot_radius * math.cos(t),
                                    cy + self.args.bot_radius * math.sin(t), cz, -t % (2 * math.pi),
                                    cell) + NO_ANIM
        self.state = state
        for other in self.server.clients.values():
            if other.in_world:
                self.server.send(other, PEER, struct.pack("<I", BOT_ID) + state)

    def move_actors(self, now):
        """As the authority, the bot places each actor of its cells bot_shift units east of where
        the last authority left it, swaying east and west by bot_sway once per bot_period."""
        server = self.server
        owned = [refid for refid, (_, key, _) in server.actors.items()
                 if server.actor_owners.get(refid, (server.owners.get(key),))[0] == BOT_ID]
        for refid in owned:
            server.actor_seen[refid] = now  # a client's states of it have stopped
        owned = [server.actors[refid][2] for refid in owned]
        phase = (now - self.start) * 2 * math.pi / self.args.bot_period
        sway = self.args.bot_sway * math.sin(phase)
        facing = math.pi / 2 if math.cos(phase) >= 0 else 3 * math.pi / 2
        for i in range(0, len(owned), ACTORS_PER_PACKET):
            chunk = owned[i:i + ACTORS_PER_PACKET]
            body = struct.pack("<I", len(chunk))
            for record in chunk:
                refid, x, y, z, heading, health, flags, magicka, fatigue, target, anim = \
                    ACTOR.unpack(record)
                if refid in self.fights:
                    flags, target = flags | ACTOR_IN_COMBAT, self.fights[refid]
                    server.actors[refid] = (BOT_ID, server.actors[refid][1], ACTOR.pack(
                        refid, x, y, z, heading, health, flags, magicka, fatigue, target, anim))
                if self.args.bot_sway:
                    heading = facing
                if self.args.bot_stats:
                    health, magicka, fatigue = (float(v) for v in self.args.bot_stats.split(","))
                body += ACTOR.pack(refid, x + self.args.bot_shift + sway, y, z, heading, health,
                                   flags, magicka, fatigue, target, anim)
            for other in server.clients.values():
                if other.in_world:
                    server.send(other, ACTORS, struct.pack("<I", BOT_ID) + body)

    def move(self, now):
        """The schedule's bounty, busy and death changes, then a step; before authority."""
        for spec in [b for b in self.bounties if self.args.bot and self.window(f"{b[0]}:", now)]:
            self.bounties.remove(spec)
            print(f"{time.strftime('%H:%M:%S')} bot bounty {spec[1]}", flush=True)
            self.server.broadcast_event(BOT_ID, EVENT_BOUNTY, struct.pack("<i", spec[1]), now)
        for spec in [j for j in self.journal if self.args.bot and self.window(f"{j[0]}:", now)]:
            self.journal.remove(spec)
            print(f"{time.strftime('%H:%M:%S')} bot journal {spec[1]} {spec[2]}", flush=True)
            self.server.share_journal(None, [spec[1:]], now)
        for spec in [f for f in self.followers if self.args.bot and self.window(f"{f[0]}:", now)]:
            self.followers.remove(spec)
            print(f"{time.strftime('%H:%M:%S')} bot is followed by {spec[1]:#010x}", flush=True)
            self.server.follow_bot(spec[1], now)
        if self.args.bot and self.anchor and self.window(self.args.bot_busy, now) != self.busy:
            self.busy = not self.busy
            print(f"{time.strftime('%H:%M:%S')} bot {'saves' if self.busy else 'is back'}",
                  flush=True)
            self.server.broadcast_event(BOT_ID, EVENT_BUSY,
                                        bytes([BUSY_SAVING if self.busy else 0]), now)
        if self.args.bot and self.anchor and self.window(self.args.bot_dead, now) != self.dead:
            self.dead = not self.dead
            print(f"{time.strftime('%H:%M:%S')} bot {'dies' if self.dead else 'respawns'}",
                  flush=True)
            self.server.broadcast_event(BOT_ID, EVENT_PLAYER,
                                        bytes([PLAYER_DEATH if self.dead else PLAYER_ALIVE]),
                                        now)
        away = self.window(self.args.bot_away, now)
        if self.args.bot and self.anchor and away != self.away:
            self.away = away
            print(f"{time.strftime('%H:%M:%S')} bot {'is away' if away else 'is back'}",
                  flush=True)
        if self.args.bot and self.anchor and now >= self.next_step and not self.busy                 and not away:
            self.next_step = now + 1 / self.args.bot_rate
            self.step(now)

    def act(self, now):
        """Its actors, then the rest of the schedule; after authority."""
        if self.args.bot and now >= self.actor_next:
            self.actor_next = now + ACTOR_PERIOD
            self.move_actors(now)
        for due, holder, refid in [b for b in self.breaks if now >= b[0]]:
            self.breaks.remove((due, holder, refid))
            print(f"{time.strftime('%H:%M:%S')} bot breaks client {holder}'s hold on "
                  f"{refid:#010x}", flush=True)
            self.server.send_event(holder, BOT_ID, EVENT_HOLD_BROKEN,
                                   struct.pack("<III", refid, holder, 2),
                                   now)
        if self.args.bot_hold:
            refid, _, span = self.args.bot_hold.partition("@")
            refid = int(refid, 16)
            want = self.window(span, now)
            owner = self.server.actor_authority(refid)
            if want != self.held and owner:
                self.held = want
                print(f"{time.strftime('%H:%M:%S')} bot {'holds' if want else 'releases'} "
                      f"{refid:#010x} (authority {owner})", flush=True)
                self.server.send_event(owner, BOT_ID, EVENT_HOLD,
                                       struct.pack("<III", refid, owner, want), now)
        if self.args.bot_kill and not self.killed:
            refid, _, at = self.args.bot_kill.partition("@")
            refid = int(refid, 16)
            if self.window(at, now):
                self.killed = True
                self.server.world.deaths[refid] = BOT_ID
                print(f"{time.strftime('%H:%M:%S')} bot kills {refid:#010x}", flush=True)
                self.server.broadcast_event(BOT_ID, EVENT_DEATH, struct.pack("<I", refid), now)
        for spec in [b for b in self.statuses if self.window(b.partition("@")[2], now)]:
            self.statuses.remove(spec)
            refid, _, values = spec.partition("@")[0].partition(":")
            refid, values = int(refid, 16), tuple(int(v) for v in values.split(","))
            self.server.world.statuses[refid] = values
            print(f"{time.strftime('%H:%M:%S')} bot sets {describe_status(refid, values)}",
                  flush=True)
            self.server.broadcast_event(BOT_ID, EVENT_STATUS, STATUS.pack(refid, *values), now)
        for spec in [b for b in self.affects if self.window(b.rpartition("@")[2], now)]:
            self.affects.remove(spec)
            refid, index, name = spec.rpartition("@")[0].split(":", 2)
            print(f"{time.strftime('%H:%M:%S')} bot gives {refid} effect {index} of {name}",
                  flush=True)
            self.server.broadcast_event(BOT_ID, EVENT_AFFECT,
                                        struct.pack("<IB", int(refid, 16), int(index))
                                        + name.encode("latin-1") + b"\0", now)
        if self.args.bot_hit and not self.hit:
            refid, _, at = self.args.bot_hit.partition("@")
            refid = int(refid, 16)
            owner = self.server.actor_authority(refid)
            if owner and self.window(at, now):
                self.hit = True
                print(f"{time.strftime('%H:%M:%S')} bot hits {refid:#010x} for 5 (authority "
                      f"{owner})", flush=True)
                self.server.send_event(owner, BOT_ID, EVENT_HIT,
                                       struct.pack("<IIf", refid, owner, 5.0), now)
        for due, index, value in [w for w in self.weather if self.window(f"{w[0]}:", now)]:
            self.weather.remove((due, index, value))
            self.server.set_weather(BOT_ID, {index: value}, time.strftime("%H:%M:%S"), now)
        if self.args.bot_hit_player and not self.hit_player:
            damage, _, at = self.args.bot_hit_player.partition("@")
            damage, _, fatigue = damage.partition(":")
            if self.window(at, now):
                self.hit_player = True
                for other in [c for c in self.server.clients.values() if c.alive]:
                    print(f"{time.strftime('%H:%M:%S')} bot hits client {other.id} for {damage}"
                          f" health, {fatigue or 0} fatigue", flush=True)
                    self.server.send_event(other.id, BOT_ID, EVENT_PLAYER_HIT,
                                           struct.pack("<IIff", 0, other.id, float(damage),
                                                float(fatigue or 0)), now)
        for spell in [s for s in self.spells if self.window(f"{s[0]}:", now)]:
            _, kind, name, refid = spell
            if refid:
                target = self.server.actor_authority(refid)
            else:
                target = next((c.id for c in self.server.clients.values() if c.alive), None)
            if not target or target == BOT_ID:
                continue
            self.spells.remove(spell)
            verb = "casts" if kind == EVENT_SPELL else "is seen casting"
            if refid is None:
                print(f"{time.strftime('%H:%M:%S')} bot {verb} {name} at nothing", flush=True)
                self.server.broadcast_event(BOT_ID, kind,
                                            SPELL.pack(0, 0, 0, SOURCE_SPELL, 1) + zstr(name),
                                            now)
                continue
            on = f"{refid:#010x}" if refid else "the player"
            print(f"{time.strftime('%H:%M:%S')} bot {verb} {name} on {on} of client {target}",
                  flush=True)
            data = SPELL.pack(0, target, refid, SOURCE_SPELL, 1) + zstr(name)
            if kind == EVENT_SPELL:
                self.server.send_event(target, BOT_ID, kind, data, now)
            else:
                self.server.broadcast_event(BOT_ID, kind, data, now)
        for spec in [s for s in self.spawns if self.window(f"{s[0]}:", now)]:
            self.spawns.remove(spec)
            _, x, y, z = self.anchor
            self.server.add_spawn(BOT_ID, {"cell": spec[2], "count": 1, "pos": [x + 64, y, z],
                                           "rot": [0.0, 0.0, 0.0], "id": spec[1],
                                           "data": spec[3] is not None, "condition": spec[3] or 0,
                                           "charge": 0},
                                  time.strftime("%H:%M:%S"), now)
        for box in [b for b in self.boxes if self.window(f"{b[0]}:", now)]:
            self.boxes.remove(box)
            self.server.set_contents(BOT_ID, box[1], box[2], box[3], False,
                                     time.strftime("%H:%M:%S"),
                                     now)
        for at in [t for t in self.takes if self.window(f"{t}:", now)]:
            self.takes.remove(at)
            for sid in [s for s, v in self.server.world.spawns.items() if v["origin"] != BOT_ID]:
                self.server.remove_spawn(BOT_ID, sid, time.strftime("%H:%M:%S"), now)
        for shot in [s for s in self.shots if self.window(f"{s[0]}:", now)]:
            self.shots.remove(shot)
            print(f"{time.strftime('%H:%M:%S')} bot shoots {shot[1]}", flush=True)
            self.server.broadcast_event(BOT_ID, EVENT_SHOT,
                                        SHOT.pack(0, 1.0, 0.0, 1) + zstr(shot[1]),
                                        now)
        if self.args.bot_say and self.anchor and \
                now >= self.said + self.args.bot_say:
            self.said = now
            self.line += 1
            self.server.broadcast_event(BOT_ID, EVENT_TEXT, b"bot %d" % self.line, now)
