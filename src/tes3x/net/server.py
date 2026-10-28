"""Session server, retained world and player state, and test bot."""

import glob
import math
import os
import queue
import random
import select
import signal
import socket
import struct
import sys
import textwrap
import threading
import time
import traceback
import tes3x.netbuild as tes3x_netbuild
from .proto import (ACTOR, ACTOR_EFFECT_ACTORS, ACTOR_MOVE, ACTOR_MOVES_MAX, ACTOR_PLACE,
                    ACTOR_PLACES_PER_EVENT, ACTOR_STATS_MAX, ACTORS, ACTORS_KEPT_MAX, ADMIN_PORT,
                    AI_FOLLOW, AI_NO_DESTINATION, AI_PACKAGES_MAX, AI_TARGET_CLIENT,
                    AI_TARGET_OBJECT, AI_TARGET_PLAYER, ANNOUNCE_WAIT, AUTHORITY_PERIOD, BAN_KINDS,
                    BOT_ID, BUILD, BULK_ACK, BULK_ACK_BODY, BULK_ACK_EVERY, BULK_CHUNK, BULK_DONE,
                    BULK_NAME, BULK_NO_SPACE, BULK_OFFER, BULK_RECEIVING, BULK_STATUS, BYE,
                    CAIUS_PACKAGE, cell_keys, CELL_UNITS, CHARACTER_FILE, CHARACTERS_LISTED,
                    checkpoint_name, CHUNK, CLOCK, CLOCK_INTERVAL, CONTENTS_HEAD, CONTENTS_ROLLED,
                    describe_contents, describe_key, describe_local, describe_object,
                    describe_spawn, describe_state, describe_status, describe_weather, DHCP_CLIENT,
                    DHCP_SERVER, DNS_PORT, EVENT_ACTOR_EFFECTS, EVENT_ACTOR_EQUIPMENT,
                    EVENT_ACTOR_MOVE, EVENT_ACTOR_PLACE, EVENT_ACTOR_STATS, EVENT_AFFECT,
                    EVENT_AI_PACKAGE, EVENT_AUTHORITY, EVENT_BOUNTY, EVENT_BUSY, EVENT_CAST,
                    EVENT_CHARS, EVENT_CONTENTS, EVENT_DATA, EVENT_DEATH, EVENT_EQUIPMENT,
                    EVENT_GAME, EVENT_HIT, EVENT_HOLD, EVENT_HOLD_BROKEN, EVENT_IDENTITY,
                    EVENT_KILL, EVENT_KILLS, EVENT_LOAD, EVENT_LOCALS, EVENT_NEWCHAR, EVENT_OBJECTS,
                    EVENT_OFFER, EVENT_OWNERS, EVENT_PICK, EVENT_PLAYER, EVENT_PLAYER_HIT,
                    EVENT_RECORD, EVENT_REMOVE, EVENT_REST, EVENT_RULES, EVENT_RUN, EVENT_SAVE,
                    EVENT_SHOT, EVENT_SNAPSHOT, EVENT_SPAWN, EVENT_SPELL, EVENT_STATUS, EVENT_TEXT,
                    EVENT_WANT, EVENT_WEATHER, EVENT_WELCOME, EVENTS, EVENTS_GAP, EVENTS_HEAD,
                    FACTION_ALL, FACTION_JOINED, FACTION_RANK, FACTION_REPUTATION, FACTIONS_MAX,
                    fingerprint, finite, GAME_NEW, GAME_NONE, GONE, GUEST_IP, HANDSHAKE1,
                    HANDSHAKE2, HANDSHAKE3, HANDSHAKE_KEEP, HANDSHAKE_PAD, HANDSHAKE_RATE,
                    HANDSHAKE_RATE_ALL, HANDSHAKES_PENDING, HEARTBEAT, HELLO, HELLO_BODY, IN_WORLD,
                    INNER, JOURNAL_QUESTS_MAX, KEEP, KEY, KEY_EXTERIOR, KEY_INTERIOR, KILLS_MAX,
                    LOBBY, LOCALS_MAX, LOG_LEVELS, MAGIC, MANAGER, MANAGER_VERSION, NO_CELL, Noise,
                    NOISE_TAG, now_us, OUTER, OWNER_PAIR, OWNER_STALE, OWNERS_PER_EVENT,
                    PACE_PACKETS, PACE_WINDOW, pack_actor_effects, pack_actor_stats,
                    pack_ai_package, pack_contents, pack_factions, pack_globals, pack_journal,
                    pack_kills, pack_locals, pack_names, pack_objects, pack_spawn, pack_topics,
                    pack_weather, pack_world_script, PASSWORD_RATE, PEER, PEER_IP, PICK_CHARACTER,
                    PICK_NEW, PICK_START, PLACE_HOLD, placeable, plain_name, PLAYER_ALIVE,
                    PLAYER_DEATH, PLAYER_EFFECT_BYTES, PLAYER_FACTIONS, PLAYER_JOURNAL,
                    PLAYER_READY, PLAYER_RESPAWN, PLAYER_TOPICS, PLAYER_TOPICS_MAX, PROLOGUE,
                    QUEST_INDICES, record_parts, RECORD_PARTS_MAX, RECORDS_MAX, REFUSE, REFUSE_BODY,
                    REFUSED_BANNED, REFUSED_FULL, REFUSED_KICKED, REFUSED_LOAD_ORDER,
                    REFUSED_PASSWORD, REFUSED_PROTOCOL, REFUSED_STALE, RELAY, RESEND, RESPAWN,
                    RESPAWN_PLACES, REST_ASK, REST_CLOSE, REST_OPEN, REST_START, RULE_AUTO_LEVEL,
                    same_place, sane_clock, SCRIPT_RUNNING, SCRIPT_STOPPED, SCRIPT_VALUES,
                    SCRIPT_VALUES_MAX, seal, SEALED, SERVER_EVENTS, SHOT, SPAWN, SPAWN_IDS, SPELL,
                    SPYMASTER_DONE, SPYMASTER_GIVEN, SPYMASTER_QUEST, STARTS, STATE, STATE_BODY,
                    STATE_SIZE, STATUS, STREAM_NAME, T3MP, T3MP_VERSION, TARGETED, TEXT_PRIVATE,
                    unpack_actor_effect_part, unpack_actor_effects, unpack_actor_equipment,
                    unpack_actor_stats, unpack_ai_package, unpack_contents, unpack_equipment,
                    unpack_factions, unpack_globals, unpack_identity, unpack_journal, unpack_kill,
                    unpack_locals, unpack_objects, unpack_record, unpack_removes, unpack_spawn,
                    unpack_weather, unpack_world_script, unseal, WEATHER_OFFER, WELCOME, wire_text,
                    WORLD_GLOBALS, WORLD_GLOBALS_MAX, WORLD_SCRIPTS, WORLD_SCRIPTS_MAX, zstr)
from .tunnel import arp_frame, dhcp_reply, dns_reply, Tunnel, udp_frame, udp_from_frame, udp_socket
from .validation import valid_client_event
from .access import (ban_value, load_admin_secret, load_admitted, load_bans, load_password,
                     load_server_key, save_bans)
from .admin import quiet_admin, RemoteAdmin
from .bot import Bot
from .state import (assign_authority, assign_owners, keep_character, kept_characters, load_starts,
                    new_character_folder, save_player, spawn_twin, World)
from .stream import PlayerStream
from .transport import Bucket, Client, Clock, Incoming, Outgoing, Reliable, SourceBuckets, summary


def decode(payload):
    """(seq, count, mac) of a broadcast test datagram, or None."""
    if len(payload) < 22 or payload[:8] != MAGIC:
        return None
    seq, count = struct.unpack_from("<II", payload, 8)
    return seq, count, payload[16:22].hex(":")


class Server:
    """A session server: welcomes consoles by their key, answers each heartbeat at once and relays
    each client's state to the others. Each --tunnel also serves an xemu guest."""

    def __init__(self, args):
        self.args = args

    def dropped(self, direction):
        if self.args.drop and self.loss.random() < self.args.drop:
            self.lost[direction] += 1
            return True
        return False

    def send(self, client, kind, body=b""):
        """Seal and queue a packet; nothing goes out before the handshake has keyed the client."""
        client.seq += 1
        if client.keys is None:
            return
        inner = INNER.pack(kind, client.peer_seq, now_us(), client.peer_time) + body
        outer = OUTER.pack(b"T3MP", client.version, SEALED, 0, client.session, client.seq)
        packet = outer + seal(client.keys[1], client.seq, outer, inner)
        if self.dropped("out"):
            return
        client.queue.append((client.addr, packet, client.seq))
        self.pump(client)

    def transmit(self, addr, packet, ident=0):
        if len(addr) == 4:  # a tunnel guest, by its MAC
            ip, port, mac, link = addr
            link.send(udp_frame(mac, ip, packet, ident, sport=self.args.port, dport=port))
        else:
            self.sock.sendto(packet, addr)

    def pump(self, client):
        """Send what PACE_PACKETS allows of the client's queue."""
        now = time.monotonic()
        start, count = client.window
        if now - start >= PACE_WINDOW:
            start, count = now, 0
        while client.queue and count < PACE_PACKETS:
            addr, packet, seq = client.queue.pop(0)
            self.transmit(addr, packet, seq)
            count += 1
        client.window = (start, count)

    def adopt_world(self, order, now):
        """Load what --world holds for this load order: the clock, deaths, objects, weather."""
        if not self.args.world:
            return
        path = os.path.join(self.args.world, f"{order:08x}.json")
        if not self.world.load(path, now, clock=self.args.hour is None):
            print(f"world {path}: new", flush=True)
            return
        print(f"world {path}: {self.world}", flush=True)

    def notify(self, text, now, only=None, skip=None):
        """Show text on the screen of every console in the world, or of one client, in as many
        notices as it needs."""
        lines = [line.encode("latin-1", "replace")
                 for line in textwrap.wrap(text, EVENT_DATA, break_on_hyphens=False)]
        for other in self.clients.values():
            if other.in_world and other is not skip and (only is None or other is only):
                for data in lines:
                    other.rel.queue(EVENT_TEXT, 0, data)
                self.flush(other, now)

    def combat_leave(self):
        """Seconds a player in combat waits to leave, in EVENT_RULES' second byte."""
        return max(0, min(120, int(getattr(self.args, "combat_leave", 10))))

    def rules(self):
        """The flags EVENT_RULES gives each console."""
        return RULE_AUTO_LEVEL if getattr(self.args, "level_up", "rest") == "auto" else 0

    def listed_name(self, client):
        """The player's name for `admin list`, or empty before the server knows it."""
        parts = self.identities.get(client.id)
        return self.player_name(client) if (parts and parts[0]) or client.character else ""

    def player_name(self, client):
        parts = self.identities.get(client.id)
        if parts and parts[0]:
            return unpack_identity(parts[0])[2]
        return client.character or f"Player {client.id}"

    def announce_join(self, client, now):
        """Tell the others once the character's name is known, or after a few seconds without."""
        client.relaunching = False
        if client.announced or client.lobby:
            return
        parts = self.identities.get(client.id)
        if not (parts and parts[1]) and now - client.joined < ANNOUNCE_WAIT:
            client.announce_due = True
            return
        client.announced, client.announce_due = True, False
        self.notify(f"{self.player_name(client)} has joined.", now, skip=client)
        if self.args.welcome:
            client.rel.queue(EVENT_WELCOME, 0,
                             self.args.welcome[:EVENT_DATA].encode("latin-1", "replace"))
            self.flush(client, now)

    def leave(self, client):
        # a relaunch to load a character is not a departure
        if client.in_world and client.announced and not client.relaunching:
            self.notify(f"{self.player_name(client)} has left.", time.monotonic(), skip=client)
            client.announced = False
        client.alive = False
        self.scripts_orphaned(client.id, time.monotonic())
        for refid, (holder, target) in list(self.dialogues.items()):
            if holder == client.id:
                del self.dialogues[refid]
                if target != holder:
                    self.send_event(target, holder, EVENT_HOLD,
                                    struct.pack("<III", refid, target, 0), time.monotonic())
        for other in self.clients.values():
            if other.alive:
                self.send(other, GONE, struct.pack("<I", client.id))
        self.rest_menus.discard(client.id)
        if self.rest_by == client.id:
            self.rest_cancel(client, time.monotonic())
        else:
            self.rest_check(time.monotonic())

    def flush(self, client, now, resend=True):
        """Send the ack and the unacked events, or hold them until EVENTS_GAP has passed."""
        if now - client.rel.last_send < EVENTS_GAP:
            client.flush_due = True
            return
        client.flush_due = False
        self.send(client, EVENTS, client.rel.packet(now, resend or bool(client.rel.out)))

    def broadcast_event(self, origin, kind, data, now):
        for other in self.clients.values():
            if other.in_world and other.id != origin:
                other.rel.queue(kind, origin, data)
                self.flush(other, now)

    def send_event(self, target, origin, kind, data, now):
        for other in self.clients.values():
            if other.alive and other.id == target:
                other.rel.queue(kind, origin, data)
                self.flush(other, now)

    def set_weather(self, origin, entries, stamp, now, to_origin=True):
        """Record the regions whose weather changes and send them to every client."""
        changed = {i: w for i, w in entries.items() if self.world.weather.get(i) != w}
        if not changed:
            return
        self.world.weather.update(changed)
        self.world.dirty = True
        print(f"{stamp} weather from client {origin}: {describe_weather(changed)}", flush=True)
        for other in self.clients.values():
            if other.in_world and (to_origin or other.id != origin):
                for data in pack_weather(changed):
                    other.rel.queue(EVENT_WEATHER, origin, data)
                self.flush(other, now)

    def add_spawn(self, origin, spawn, stamp, now, token=0):
        """Name a reference made at run time and send it to every client, its maker too, which
        knows it as its own by cell, object and place. A repeat gets the id it already has, and
        goes to the maker only."""
        sid = spawn_twin(self.world.spawns, spawn, origin, token, now, self.world.deaths)
        if sid is not None:
            print(f"{stamp} client {origin} repeats {describe_spawn(sid, self.world.spawns[sid])}",
                  flush=True)
            self.send_event(origin, self.world.spawns[sid]["origin"], EVENT_SPAWN,
                            pack_spawn(sid, self.world.spawns[sid]),
                            now)
            return sid
        # a dead creature's placeholder rolled again
        for old, known in list(self.world.spawns.items()):
            if spawn.get("leveled") and known.get("leveled") == spawn["leveled"] and \
                    not known["removed"]:
                self.remove_spawn(origin, old, stamp, now, to_origin=True)
        sid = SPAWN_IDS | self.world.next_spawn
        self.world.next_spawn += 1
        self.world.spawns[sid] = dict(spawn, origin=origin, token=token, made=now, removed=False)
        self.world.dirty = True
        print(f"{stamp} client {origin} made {describe_spawn(sid, self.world.spawns[sid])}",
              flush=True)
        data = pack_spawn(sid, self.world.spawns[sid])
        for other in self.clients.values():
            if other.in_world:
                other.rel.queue(EVENT_SPAWN, origin, data)
                self.flush(other, now)
        return sid

    def remove_spawn(self, origin, sid, stamp, now, to_origin=False):
        spawn = self.world.spawns.get(sid)
        if spawn is None or spawn["removed"]:
            return
        spawn["removed"] = True
        self.world.dirty = True
        print(f"{stamp} client {origin} removed {describe_spawn(sid, spawn)}", flush=True)
        if to_origin:
            self.send_event(origin, 0, EVENT_SPAWN, pack_spawn(sid, spawn), now)
        self.broadcast_event(origin, EVENT_SPAWN, pack_spawn(sid, spawn), now)

    def send_contents(self, target, refid, now, flags=0):
        box = self.world.contents[refid]
        for other in self.clients.values():
            if other.alive and other.id == target:
                for part in pack_contents(refid, box["cell"], box["entries"], flags):
                    other.rel.queue(EVENT_CONTENTS, box["origin"], part)
                self.flush(other, now)

    def set_contents(self, origin, refid, cell, entries, rolled, stamp, now):
        """Keep a container's contents and send them to the other clients. A console's first
        reading of a container the server already holds gets the server's contents back."""
        known = self.world.contents.get(refid)
        if rolled and known:
            if known["entries"] != entries:
                print(f"{stamp} client {origin} opened {refid:#010x}: keeps "
                      f"{describe_contents(known['entries'])}", flush=True)
                self.send_contents(origin, refid, now)
            return
        self.world.contents[refid] = {"cell": cell, "entries": entries, "origin": origin}
        self.world.dirty = True
        print(f"{stamp} client {origin} {'opened' if rolled else 'changed'} {refid:#010x} in cell "
              f"{cell}: {describe_contents(entries)}", flush=True)
        for other in self.clients.values():
            if other.in_world and other.id != origin:
                self.send_contents(other.id, refid, now)

    def key_folder(self, client):
        return (os.path.join(self.args.world, "characters", fingerprint(client.key))
                if self.args.world and client.key else None)

    def dash_character(self, key):
        """The character this key plays on the server: the one in the world now, else the newest
        kept; what the Dash lists beside the server."""
        for other in self.clients.values():
            if other.alive and other.key == key and (
                    other.character or self.identities.get(other.id)):
                return self.player_name(other)
        root = (os.path.join(self.args.world, "characters", fingerprint(key))
                if self.args.world else None)
        kept = kept_characters(root, fixtures=self.args.adopt or self.args.rebuild is not None)
        if not kept:
            return ""
        try:
            return PlayerStream(os.path.join(root, kept[0][0], STREAM_NAME)).identity["name"]
        except (OSError, ValueError, TypeError, KeyError):
            return kept[0][0]

    def character_folder(self, client):
        root = self.key_folder(client)
        return os.path.join(root, client.character) if root and client.character else None

    def player_stream(self, client):
        folder = self.character_folder(client)
        if folder is None:
            return None
        if folder not in self.streams:
            self.streams[folder] = PlayerStream(os.path.join(folder, STREAM_NAME))
        return self.streams[folder]

    def keep_place(self, client, now):
        """The character's last place, from STATE, once a replayed place has been reached."""
        stream = self.player_stream(client) if client.synced else None
        body = client.state[:STATE_BODY.size]
        if stream is None or not STATE_BODY.unpack_from(body)[0] & IN_WORLD:
            return
        if client.place_hold:
            kept, until = client.place_hold
            if not same_place(kept, body) and now < until:
                return
            if not same_place(kept, body):
                print(f"{time.strftime('%H:%M:%S')} client {client.id} did not reach "
                      f"{describe_state(kept)}", flush=True)
            client.place_hold = None
        stream.keep_place(body)

    def respawn(self, client, delay, now):
        """Tell a dead player's console when and where to come back, and what it loses."""
        stream = self.player_stream(client) if client.synced else None
        gold = sum(e[0] for item, entries in stream.items.items() if item.lower() == "gold_001"
                   for e in entries) if stream else 0
        lost = gold * self.args.death_gold // 100
        client.rel.queue(EVENT_PLAYER, 0, bytes([PLAYER_RESPAWN]) + RESPAWN.pack(
            int(delay * 1000), RESPAWN_PLACES[self.args.respawn], lost))
        self.flush(client, now)
        return lost

    def on_player_death(self, client, alive, stamp, now):
        stream = self.player_stream(client) if client.synced else None
        name = self.player_name(client)
        client.dead = not alive
        if stream:
            stream.dead, stream.dirty = not alive, True
        life = bytes([PLAYER_ALIVE if alive else PLAYER_DEATH])
        for other in self.clients.values():
            if other.in_world and other is not client:
                other.rel.queue(EVENT_PLAYER, client.id, life)
        if alive:
            print(f"{stamp} client {client.id} ({name}) is back", flush=True)
            for other in self.clients.values():
                if other.in_world and other is not client:
                    self.flush(other, now)
            return
        lost = self.respawn(client, self.args.respawn_delay, now)
        print(f"{stamp} client {client.id} ({name}) died: respawns at the {self.args.respawn} "
              f"marker "
              f"in {self.args.respawn_delay:g} s, loses {lost} gold", flush=True)
        notice = f"{name} has died."[:EVENT_DATA].encode("latin-1", "replace")
        for other in self.clients.values():
            if other.in_world and other is not client:
                other.rel.queue(EVENT_TEXT, 0, notice)
                self.flush(other, now)

    def share_topics(self, names, now, origin=None):
        """The world owns learned topics; additions reach players already in the world."""
        known = {name.lower(): name for name in self.world.topics}
        added = []
        for name in names:
            if name.lower() not in known:
                if len(known) == PLAYER_TOPICS_MAX:
                    break
                known[name.lower()] = name
                added.append(name)
        if not added:
            return
        self.world.topics, self.world.dirty = list(known.values()), True
        for other in self.clients.values():
            if other is not origin and other.in_world and other.synced:
                for data in pack_topics(added):
                    other.rel.queue(EVENT_PLAYER, 0, data)
                self.flush(other, now)

    def share_journal(self, client, quests, now):
        """The journal is the world's and only moves forward: a quest keeps the indices players
        reached, and a new one reaches the other players already in the world."""
        changed = []
        for quest, index in quests:
            kept = self.world.journal.get(quest.lower())
            if kept is None:
                if len(self.world.journal) >= JOURNAL_QUESTS_MAX:
                    continue
                kept = self.world.journal[quest.lower()] = [quest, []]
            if kept[1] and index <= kept[1][-1]:
                continue
            kept[1].append(index)
            del kept[1][:-QUEST_INDICES]
            changed.append((kept[0], index))
        if not changed:
            return
        self.world.dirty = True
        for other in self.clients.values():
            if other is not client and other.in_world and other.synced:
                for data in pack_journal(changed):
                    other.rel.queue(EVENT_PLAYER, 0, data)
                self.flush(other, now)

    def journal_replay(self):
        return pack_journal([(quest, index) for quest, indices in
                             sorted(self.world.journal.values(), key=lambda q: q[0].lower())
                             for index in indices])

    def share_factions(self, client, entries, before, now):
        """Rank and reputation are the world's, membership and expulsion each character's.
        A member who already was one moves the world rank; a character who has just joined
        takes the world's rank instead."""
        shared, corrections = [], []
        for faction, bits, rank, reputation in entries:
            key = faction.lower()
            world = self.world.factions.get(key)
            if world is None:
                if len(self.world.factions) >= FACTIONS_MAX:
                    continue
                world = self.world.factions[key] = {"id": faction, "rank": None,
                                                    "reputation": 0}
                self.world.dirty = True
            member = bits & FACTION_JOINED
            was_member = (before.get(key) or [None, 0, 0, 0])[3] & FACTION_JOINED
            changed = False
            if world["reputation"] != reputation:
                world["reputation"], changed = reputation, True
            if member and (was_member or world["rank"] is None) and world["rank"] != rank:
                world["rank"], changed = rank, True
            elif member and not was_member and world["rank"] != rank:
                corrections.append((world["id"], FACTION_RANK, world["rank"], reputation))
            if changed:
                self.world.dirty = True
                shared.append((world["id"], FACTION_REPUTATION | (
                    FACTION_RANK if world["rank"] is not None else 0), world["rank"] or 0,
                    world["reputation"]))
        for data in pack_factions(corrections):
            client.rel.queue(EVENT_PLAYER, 0, data)
        if corrections:
            self.flush(client, now)
        if not shared:
            return
        for other in self.clients.values():
            if other is not client and other.in_world and other.synced:
                for data in pack_factions(shared):
                    other.rel.queue(EVENT_PLAYER, 0, data)
                self.flush(other, now)

    def share_globals(self, client, entries, now):
        """The world keeps each global as its latest writer left it; changes reach the other
        players already in the world. Two consoles writing one at once can end apart."""
        changed = []
        for name, value in entries:
            key = name.lower()
            kept = self.world.globals.get(key)
            if kept and struct.pack("<f", kept[1]) == struct.pack("<f", value):
                continue
            if kept is None and len(self.world.globals) >= WORLD_GLOBALS_MAX:
                continue
            self.world.globals[key] = [kept[0] if kept else name, value]
            changed.append((self.world.globals[key][0], value))
        if not changed:
            return None
        self.world.dirty = True
        for other in self.clients.values():
            if other is not client and other.in_world and other.synced:
                for data in pack_globals(changed, WORLD_GLOBALS):
                    other.rel.queue(EVENT_PLAYER, 0, data)
                self.flush(other, now)
        return "world globals " + ", ".join(f"{name} {value:g}" for name, value in changed)

    def faction_replay(self, stream, replay):
        """Each faction's world rank and reputation, with the character's own flags on a replay."""
        own = (stream.factions or {}) if replay else {}
        for key, (faction, rank, reputation, flags) in own.items():
            if key not in self.world.factions and len(self.world.factions) < FACTIONS_MAX:
                self.world.factions[key] = {"id": faction, "rank": rank if flags & FACTION_JOINED
                                            else None, "reputation": reputation}
                self.world.dirty = True
        entries = []
        for key, world in sorted(self.world.factions.items()):
            kept = own.get(key)
            if replay:
                rank = world["rank"] if world["rank"] is not None else kept[1] if kept else 0
                entries.append((world["id"], FACTION_ALL | (kept[3] if kept else 0), rank,
                                world["reputation"]))
            else:
                entries.append((world["id"], FACTION_REPUTATION | (
                    FACTION_RANK if world["rank"] is not None else 0), world["rank"] or 0,
                    world["reputation"]))
        return pack_factions(entries)

    def player_ready(self, client, replay, stamp, now):
        """Replay retained state, or have the console publish its supported fields."""
        self.announce_join(client, now)
        self.replay_records(client)
        if replay:
            for sid, spawn in list(self.world.spawns.items()):
                if spawn.get("summon") and spawn["origin"] == client.id and not spawn["removed"]:
                    self.remove_spawn(client.id, sid, stamp, now, to_origin=True)
        stream = self.player_stream(client)
        if stream is None:
            return
        self.share_topics(stream.topics or [], now, origin=client)
        self.share_journal(client, [(quest, index) for quest, indices in stream.journal.items()
                                    for index in indices], now)
        events = [data for data in stream.replay()
                  if data[0] not in (PLAYER_TOPICS, PLAYER_JOURNAL)] if replay else []
        events += self.journal_replay()
        events += pack_topics(self.world.topics)
        events += self.faction_replay(stream, replay)
        events += pack_globals(sorted(self.world.globals.values()), WORLD_GLOBALS)
        events += self.scripts_assign(client)
        client.place_hold = (stream.place, now + PLACE_HOLD) if (
            replay and stream.place and not stream.dead) else None
        for data in events:
            client.rel.queue(EVENT_PLAYER, 0, data)
        for refid, entry in sorted(self.world.ai.items()):
            data = self.ai_package_for(client, refid, entry)
            if data is not None:
                client.rel.queue(EVENT_AI_PACKAGE, 0, data)
            if entry["target"] == ["player", client.character]:
                for other in self.clients.values():
                    if other.in_world and other.id != client.id:
                        self.send_ai_package(other, refid, entry, now)
        if replay and stream.dead:
            print(f"{stamp} client {client.id} died before its last stop: respawns now",
                  flush=True)
            self.respawn(client, 0, now)
        client.rel.queue(EVENT_PLAYER, 0, bytes([PLAYER_READY, 0]))
        self.flush(client, now)
        print(f"{stamp} client {client.id}: " + (
            "replayed " + (f"{stream.identity['name']}'s identity, " if stream.identity else "")
            + f"{len(stream.items)} items, {len(stream.skills)} skills, "
            f"{len(self.world.journal)} quests" + (", the level" if stream.level else "")
            + (f", the place ({describe_state(stream.place)})" if stream.place else "")
            if replay else "streams its player from scratch"), flush=True)

    def send_names(self, client, kind, names, now):
        for part in pack_names(names):
            client.rel.queue(kind, 0, part)
        self.flush(client, now)

    def offer_starts(self, client, stamp, now):
        """Have the console make a character: in this launch if it is a New Game, else it
        relaunches into one and is offered the start points again."""
        self.creating.add(fingerprint(client.key))
        client.synced = client.launch == GAME_NEW
        self.send_names(client, EVENT_NEWCHAR, [name for name, _ in self.starts], now)
        print(f"{stamp} client {client.id} makes a new character"
              + ("" if client.synced else ", after a New Game"), flush=True)

    def send_character(self, client, folder, path, loaded, stamp, now):
        """The character seed for a New Game; a missing identity cannot load a character.
        Keep its name in the folder to recognise the next launch after a restart."""
        if folder not in self.streams:
            self.streams[folder] = PlayerStream(os.path.join(folder, STREAM_NAME))
        data = self.streams[folder].character_file(sorted(self.world.globals.values()))
        if data is None:
            client.rel.queue(EVENT_TEXT, 0, b"This character has no retained identity.")
            self.flush(client, now)
            return
        name = checkpoint_name(data, CHARACTER_FILE)
        for f in os.listdir(folder):
            if f.lower().endswith(".t3c") and f != name:
                os.remove(os.path.join(folder, f))
        with open(os.path.join(folder, name), "wb") as stream:
            stream.write(data)
        client.bulk = Outgoing(name, data)
        client.rel.queue(EVENT_OFFER, 0, client.bulk.offer())
        client.rel.queue(EVENT_LOAD, 0, zstr(name))
        client.relaunching = True
        self.flush(client, now)
        print(f"{stamp} client {client.id} loaded {loaded or 'no save'}: sending "
              f"{os.path.basename(folder)}'s state as {name} ({len(data)} bytes) to start from",
              flush=True)

    def on_game(self, client, token, loaded, launch, stamp, now):
        """A console's launch: it runs one of its key's characters, or chooses one, or makes
        one."""
        if token != client.game:
            client.game, client.synced, client.character = token, False, None
            client.launch, client.rebuild = launch, None
        making = client.key and fingerprint(client.key) in self.creating
        if client.synced:  # a rejoin of the same launch: events in flight were dropped
            if client.character:
                self.player_ready(client, False, stamp, now)
            elif making:
                self.offer_starts(client, stamp, now)
            return
        kept = kept_characters(self.key_folder(client),
                               fixtures=self.args.adopt or self.args.rebuild is not None)
        for folder, path in kept:
            if loaded.lower().endswith(".t3c") and os.path.exists(
                    os.path.join(self.key_folder(client), folder, loaded.lower())):
                client.synced, client.character = True, folder
                self.creating.discard(fingerprint(client.key))
                if self.args.rebuild is not None:
                    client.rebuild = (path, now + self.args.rebuild)
                print(f"{stamp} client {client.id} runs {folder}, started from {loaded}",
                      flush=True)
                self.player_ready(client, True, stamp, now)
                return
            if self.args.rebuild is not None and path.lower().endswith(".ess"):
                with open(path, "rb") as stream:
                    matches = loaded.lower() == checkpoint_name(stream.read())
                if matches:
                    client.synced, client.character = True, folder
                    self.creating.discard(fingerprint(client.key))
                    print(f"{stamp} client {client.id} runs {folder} ({os.path.basename(path)})",
                          flush=True)
                    self.player_ready(client, True, stamp, now)
                    return
        if self.args.rebuild is not None and kept:
            client.character, path = kept[0]
            client.rebuild = (path, now + self.args.rebuild)
            print(f"{stamp} client {client.id} loaded {loaded or 'no save'}: rebuilding "
                  f"{client.character} over it from the kept state alone", flush=True)
            self.player_ready(client, True, stamp, now)
            return
        if making or (not kept and not self.args.adopt and self.key_folder(client)):
            self.offer_starts(client, stamp, now)
            return
        if not kept:
            client.synced = True
            print(f"{stamp} client {client.id} has no kept character", flush=True)
            return
        if self.args.adopt:
            self.send_character(client, os.path.join(self.key_folder(client), kept[0][0]),
                                kept[0][1],
                                loaded, stamp, now)
            return
        client.listed = [folder for folder, _ in kept[:CHARACTERS_LISTED]]
        self.send_names(client, EVENT_CHARS, client.listed, now)
        print(f"{stamp} client {client.id} loaded {loaded or 'no save'}: offered "
              f"{', '.join(client.listed)}", flush=True)

    def compare_rebuild(self, client, stamp):
        """Diff a rebuilt character's save against its checkpoint, by coverage row."""
        import io
        from tes3x.ess import report_diff
        checkpoint, _ = client.rebuild
        out = client.upload.path[:-4] + ".diff.txt"
        text = io.StringIO()
        try:
            count = report_diff(checkpoint, client.upload.path, 1000, text)
        except ValueError as exc:
            print(f"{stamp} client {client.id} rebuilt {client.character}, but the saves do "
                  f"not compare: {exc}", flush=True)
            return
        with open(out, "w", encoding="utf-8") as f:
            f.write(text.getvalue())
        print(f"{stamp} client {client.id} rebuilt {client.character}: {count} differences "
              f"from {os.path.basename(checkpoint)}, in {out}", flush=True)

    def spymaster_done(self):
        """Whether any kept character has finished the first main quest, which takes the package
        away from Caius's desk: its journal reached the closing index."""
        folders = list(self.streams)
        if self.args.world:
            for path in glob.glob(os.path.join(self.args.world, "characters", "*", "*",
                                               STREAM_NAME)):
                folders.append(os.path.dirname(path))
        for folder in dict.fromkeys(folders):
            stream = self.streams.get(folder) or PlayerStream(os.path.join(folder, STREAM_NAME))
            for quest, indices in stream.journal.items():
                if quest.lower() == SPYMASTER_QUEST and any(i >= SPYMASTER_DONE for i in indices):
                    return True
        return False

    def new_character_kit(self):
        """What chargen's Sellus Gravius would have given: the starting gold, and unless another
        character has already delivered it, Caius Cosades's package with the quest."""
        lines = ([f'Player->AddItem "Gold_001" {self.args.start_gold}'] if self.args.start_gold
                 else [])
        if not self.spymaster_done():
            lines += [f'Player->AddItem "{CAIUS_PACKAGE}" 1',
                      f"Journal {SPYMASTER_QUEST} {SPYMASTER_GIVEN}"]
        return lines

    def on_pick(self, client, what, index, stamp, now):
        if what == PICK_CHARACTER and index == PICK_NEW:
            self.offer_starts(client, stamp, now)
        elif what == PICK_CHARACTER and index < len(client.listed):
            self.creating.discard(fingerprint(client.key))
            folder = os.path.join(self.key_folder(client), client.listed[index])
            path = os.path.join(folder, STREAM_NAME)
            if os.path.isfile(path):
                self.send_character(client, folder, path, "the list", stamp, now)
        elif (what == PICK_START and index < len(self.starts) and client.synced
              and client.character is None):
            folder = new_character_folder(self.key_folder(client), "character")
            os.makedirs(folder)
            client.character = os.path.basename(folder)
            client.naming = True
            self.player_stream(client).reset()
            self.player_ready(client, False, stamp, now)
            name, lines = self.starts[index]
            for line in lines + self.new_character_kit() + [""]:
                client.rel.queue(EVENT_RUN, 0, zstr(line))
            self.flush(client, now)
            print(f"{stamp} client {client.id} starts at {name}", flush=True)

    def received(self, client, stamp, now):
        """Adopt or compare an uploaded save only in an explicit diagnostic session."""
        if not client.upload.name.lower().endswith(".ess") or not (
                self.args.adopt or self.args.rebuild is not None):
            return
        if client.rebuild and client.rebuild[1] is None:
            self.compare_rebuild(client, stamp)
            return
        if not client.synced:
            print(f"{stamp} client {client.id} sent {client.upload.name} from a game that is not "
                  f"its character's; left in uploads", flush=True)
            return
        new = client.character is None
        player = save_player(client.upload.path)
        if not player:
            print(f"{stamp} client {client.id} sent {client.upload.name}, not a save; left in "
                  f"uploads", flush=True)
            return
        folder = (new_character_folder(self.key_folder(client), player) if new
                  else self.character_folder(client))
        head = keep_character(client.upload.path, folder)
        print(f"{stamp} client {client.id} kept {client.upload.name}: {head['player']} in "
              f"{head['cell']}, {len(head['masters'])} masters"
              + (f", a new character in {os.path.basename(folder)}" if new else ""), flush=True)
        client.kept += 1
        if new:
            client.character = os.path.basename(folder)
            self.creating.discard(fingerprint(client.key))
            self.player_ready(client, False, stamp, now)
        else:
            self.player_stream(client).checkpoint()

    def on_event(self, client, kind, data, stamp, now):
        """Validate a client's event before handling or relaying it."""
        client.events += 1
        if kind in SERVER_EVENTS:
            print(f"{stamp} client {client.id} sent server event {kind}: dropped", flush=True)
            return
        if not valid_client_event(kind, data):
            return
        handler = self.EVENT_HANDLERS.get(kind)
        relay = handler(self, client, kind, data, stamp, now) if handler else KEEP
        if relay:
            self.broadcast_event(client.id, kind, data, now)

    def event_offer(self, client, kind, data, stamp, now):
        if len(data) <= BULK_OFFER.size:
            return RELAY
        ident, size, digest = BULK_OFFER.unpack_from(data)
        name = wire_text(data[BULK_OFFER.size:].split(b"\0", 1)[0])
        if client.upload and client.upload.stream:
            client.upload.stream.close()
        folder = (os.path.join(self.args.world, "uploads", fingerprint(client.key))
                  if self.args.world and client.key else None)
        client.upload = Incoming(folder, ident, size, digest, name, now)
        print(f"{stamp} client {client.id} offers {name} ({size} bytes, id {ident:#010x}): "
              f"{BULK_STATUS[client.upload.status]}"
              + (f" from chunk {client.upload.next}"
                 if client.upload.status == BULK_RECEIVING else ""), flush=True)
        self.send(client, BULK_ACK, client.upload.ack(now))
        if client.upload.status == BULK_DONE:
            self.received(client, stamp, now)
        return KEEP

    def event_player(self, client, kind, data, stamp, now):
        if data[:1] in (bytes([PLAYER_DEATH]), bytes([PLAYER_ALIVE])):
            self.on_player_death(client, data[0] == PLAYER_ALIVE, stamp, now)
            return KEEP
        stream = self.player_stream(client) if client.synced else None
        before = dict(stream.factions or {}) if stream else {}
        change = stream.take(data) if stream else None
        if stream and data[:1] == bytes([PLAYER_TOPICS]):
            self.share_topics(stream.topics or [], now, origin=client)
        if stream and data[:1] == bytes([PLAYER_JOURNAL]):
            self.share_journal(client, unpack_journal(data), now)
        if stream and data[:1] == bytes([PLAYER_FACTIONS]):
            self.share_factions(client, unpack_factions(data), before, now)
        if stream and data[:1] == bytes([WORLD_GLOBALS]):
            change = self.share_globals(client, unpack_globals(data, WORLD_GLOBALS), now)
        if stream and data[:1] == bytes([WORLD_SCRIPTS]):
            change = self.world_script(client, *unpack_world_script(data), now)
        if stream and client.naming and stream.identity:
            old = self.character_folder(client)
            folder = new_character_folder(self.key_folder(client), stream.identity["name"])
            stream.save()
            os.rename(old, folder)
            self.streams.pop(old)
            stream.path = os.path.join(folder, STREAM_NAME)
            self.streams[folder] = stream
            client.character, client.naming = os.path.basename(folder), False
            self.creating.discard(fingerprint(client.key))
        if change and self.detail["verbose"]:
            print(f"{stamp} client {client.id} {change}", flush=True)
        return KEEP

    def event_snapshot(self, client, kind, data, stamp, now):
        if len(data) != 4 + STATE_BODY.size:
            return RELAY
        stream = self.player_stream(client) if client.synced else None
        body = data[4:]
        flags, x, y, z, heading, cell = STATE_BODY.unpack(body)
        if (stream is None or not stream.identity or not flags & IN_WORLD or
                not placeable(x, y, z) or not finite(heading) or stream.arriving or
                stream.identity_parts or stream.worn_parts or stream.effects_parts):
            return KEEP
        stream.keep_place(body)
        stream.save()
        if self.world.path:
            self.world.save(now)
        client.snapshots += 1
        client.snapshot_saved = struct.unpack_from("<I", data)[0]
        client.rel.queue(EVENT_SNAPSHOT, 0, data[:4])
        self.flush(client, now)
        return KEEP

    def event_contents(self, client, kind, data, stamp, now):
        if len(data) < CONTENTS_HEAD.size:
            return RELAY
        refid, cell, part, parts, flags, entries = unpack_contents(data)
        have = self.arriving.get(client.id)
        if part == 0:
            have = self.arriving[client.id] = (refid, [], 0)
        if not have or have[0] != refid or have[2] != part:
            return KEEP
        self.arriving[client.id] = (refid, have[1] + entries, part + 1)
        if part + 1 == parts:
            del self.arriving[client.id]
            self.set_contents(client.id, refid, cell, have[1] + entries,
                              bool(flags & CONTENTS_ROLLED), stamp, now)
        return KEEP

    def event_want(self, client, kind, data, stamp, now):
        if not data:
            return RELAY
        cells = set(struct.unpack_from(f"<{min(data[0], (len(data) - 1) // 2)}H", data, 1))
        wanted = [refid for refid, box in self.world.contents.items() if box["cell"] in cells]
        for refid in wanted:
            self.send_contents(client.id, refid, now)
        if wanted:
            print(f"{stamp} client {client.id} loads cells {sorted(cells)}: sent "
                  f"{len(wanted)} containers", flush=True)
        return KEEP

    def event_spawn(self, client, kind, data, stamp, now):
        if len(data) <= SPAWN.size:
            return RELAY
        token, spawn = unpack_spawn(data)
        if placeable(*spawn["pos"]) and finite(*spawn["rot"]):
            self.add_spawn(client.id, spawn, stamp, now, token)
        return KEEP

    def event_remove(self, client, kind, data, stamp, now):
        if not data:
            return RELAY
        for sid in unpack_removes(data):
            self.remove_spawn(client.id, sid, stamp, now)
        return KEEP

    def event_weather(self, client, kind, data, stamp, now):
        if len(data) < 2:
            return RELAY
        flags, entries = unpack_weather(data)
        if flags & WEATHER_OFFER:
            entries = {i: w for i, w in entries.items() if i not in self.world.weather}
        self.set_weather(client.id, entries, stamp, now, not flags & WEATHER_OFFER)
        return KEEP

    def event_spell(self, client, kind, data, stamp, now):
        if len(data) <= SPELL.size:
            return RELAY
        caster, target, refid, _, player = SPELL.unpack_from(data)
        name = wire_text(data[SPELL.size:].split(b"\0")[0])
        who = f"client {client.id}" if player else f"{caster:#010x} of client {client.id}"
        on = (f"{refid:#010x} (authority {target})" if refid
              else f"the player of client {target}")
        print(f"{stamp} {who} casts {name} on {on}", flush=True)
        if target != BOT_ID:
            self.send_event(target, client.id, kind, data, now)
        return KEEP

    def event_cast(self, client, kind, data, stamp, now):
        if len(data) <= SPELL.size:
            return RELAY
        caster, target, refid, _, player = SPELL.unpack_from(data)
        name = wire_text(data[SPELL.size:].split(b"\0")[0])
        who = f"client {client.id}" if player else f"{caster:#010x} of client {client.id}"
        at = (f" at {refid:#010x} (client {target})" if refid
              else f" at the player of client {target}" if target else "")
        print(f"{stamp} {who} casts {name}{at}", flush=True)
        return RELAY

    def event_shot(self, client, kind, data, stamp, now):
        if len(data) <= SHOT.size:
            return RELAY
        firer, swing, other, player = SHOT.unpack_from(data)
        name = wire_text(data[SHOT.size:].split(b"\0")[0])
        who = f"client {client.id}" if player else f"{firer:#010x} of client {client.id}"
        print(f"{stamp} {who} shoots {name} ({swing:.2f}, {other:.2f})", flush=True)
        return RELAY

    def event_targeted(self, client, kind, data, stamp, now):
        if len(data) < 12:
            return RELAY
        refid, target = struct.unpack_from("<II", data)
        word = (f"damage {struct.unpack_from('<f', data, 8)[0]:.0f}"
                if kind in (EVENT_HIT, EVENT_PLAYER_HIT)
                else f"{struct.unpack_from('<I', data, 8)[0]}")
        if kind in (EVENT_HIT, EVENT_PLAYER_HIT) and len(data) >= 16:
            word += f", fatigue {struct.unpack_from('<f', data, 12)[0]:.0f}"
        if kind == EVENT_PLAYER_HIT:
            by = f" (attacker {refid:#010x})" if refid else ""
            print(f"{stamp} client {client.id} {TARGETED[kind]} client {target}{by}: {word}",
                  flush=True)
        else:
            print(f"{stamp} client {client.id} {TARGETED[kind]} {refid:#010x} "
                  f"(authority {target}): {word}", flush=True)
        if kind == EVENT_HOLD:
            on = struct.unpack_from("<I", data, 8)[0]
            held = self.dialogues.get(refid)
            if on and held and held[0] != client.id:
                print(f"{stamp} client {client.id} is refused dialogue with {refid:#010x}: "
                      f"client {held[0]} is talking", flush=True)
                self.send_event(client.id, 0, EVENT_HOLD_BROKEN,
                                struct.pack("<III", refid, client.id, 3), now)
                return KEEP
            if on:
                self.dialogues[refid] = (client.id, target)
            elif held and held[0] == client.id:
                del self.dialogues[refid]
        if target != BOT_ID:
            if kind != EVENT_HOLD or target != client.id:
                self.send_event(target, client.id, kind, data, now)
        elif kind == EVENT_HOLD and struct.unpack_from("<I", data, 8)[0] and \
                self.args.bot_break_hold is not None:
            self.bot.breaks.append((now + self.args.bot_break_hold, client.id, refid))
        return KEEP

    def event_death(self, client, kind, data, stamp, now):
        if len(data) < 4:
            return RELAY
        refid = struct.unpack_from("<I", data)[0]
        if refid in self.world.deaths:
            return KEEP
        self.world.deaths[refid] = client.id
        self.world.dirty = True
        print(f"{stamp} client {client.id}: {refid:#010x} died", flush=True)
        return RELAY

    def kill_counts(self):
        counts = {}
        for name in self.world.kills.values():
            counts[name] = counts.get(name, 0) + 1
        return counts

    def event_kill(self, client, kind, data, stamp, now):
        """Keep a death once, and tell every console the new count under its object id."""
        refid, name = unpack_kill(data)
        if refid in self.world.kills or len(self.world.kills) >= KILLS_MAX:
            return KEEP
        self.world.kills[refid] = name
        self.world.dirty = True
        count = self.kill_counts()[name]
        print(f"{stamp} client {client.id}: {refid:#010x} counted as {name} ({count})",
              flush=True)
        for other in self.clients.values():
            if other.in_world:
                other.rel.queue(EVENT_KILLS, 0, pack_kills(name, count))
                self.flush(other, now)
        return KEEP

    def event_status(self, client, kind, data, stamp, now):
        if len(data) < STATUS.size:
            return RELAY
        refid, *values = STATUS.unpack_from(data)
        self.world.statuses[refid] = tuple(values)
        self.world.dirty = True
        if self.detail["verbose"]:
            print(f"{stamp} client {client.id}: {describe_status(refid, values)}", flush=True)
        return RELAY

    def event_affect(self, client, kind, data, stamp, now):
        if len(data) <= 5:
            return RELAY
        refid, index = struct.unpack_from("<IB", data)
        name = wire_text(data[5:].split(b"\0")[0])
        if self.detail["verbose"]:
            print(f"{stamp} client {client.id}: {refid:#010x} takes effect {index} of "
                  f"{name}", flush=True)
        return RELAY

    def event_objects(self, client, kind, data, stamp, now):
        if not data:
            return RELAY
        changed = unpack_objects(data)
        self.world.objects.update(changed)
        self.world.dirty = True
        for refid, rest in changed.items():
            if self.detail["verbose"]:
                print(f"{stamp} client {client.id}: {describe_object(refid, *rest)}",
                      flush=True)
        return RELAY

    def event_locals(self, client, kind, data, stamp, now):
        """The latest value of each local, bounded; the event goes on to the others as it is."""
        refid, values = unpack_locals(data)
        kept = self.world.locals.get(refid, {})
        room = LOCALS_MAX - sum(len(v) for v in self.world.locals.values())
        changed = {}
        for slot, value in values.items():
            if kept.get(slot) == value or (slot not in kept and room <= 0):
                continue
            room -= slot not in kept
            changed[slot] = value
        if not changed:
            return RELAY
        kept.update(changed)
        self.world.locals[refid] = kept
        self.world.dirty = True
        if self.detail["verbose"]:
            print(f"{stamp} client {client.id}: {refid:#010x} locals "
                  + ", ".join(describe_local(*v) for v in sorted(changed.items())), flush=True)
        return RELAY

    def event_actor_stats(self, client, kind, data, stamp, now):
        """The latest value of each actor stat, bounded; the event goes on as it is."""
        refid, values = unpack_actor_stats(data)
        kept = self.world.actor_stats.get(refid, {})
        room = ACTOR_STATS_MAX - sum(len(v) for v in self.world.actor_stats.values())
        changed = {}
        for slot, value in values.items():
            if kept.get(slot) == value or (slot not in kept and room <= 0):
                continue
            room -= slot not in kept
            changed[slot] = value
        if not changed:
            return RELAY
        kept.update(changed)
        self.world.actor_stats[refid] = kept
        self.world.dirty = True
        if self.detail["verbose"]:
            print(f"{stamp} client {client.id}: {refid:#010x} stats "
                  + ", ".join(f"{s}={v:#x}" for s, v in sorted(changed.items())), flush=True)
        return RELAY

    def event_actor_effects(self, client, kind, data, stamp, now):
        """The latest complete set of an actor's active effects, bounded; each part goes on."""
        refid, part, parts, chunk = unpack_actor_effect_part(data)
        key = (client.id, refid)
        pending = self.world.actor_effect_parts.get(key) if part else None
        if part == 0:
            pending = [b"", 0, parts]
        if not pending or pending[1:] != [part, parts]:
            self.world.actor_effect_parts.pop(key, None)
            return None
        pending[0] += chunk
        pending[1] += 1
        self.world.actor_effect_parts[key] = pending
        if pending[1] < parts:
            return RELAY
        del self.world.actor_effect_parts[key]
        try:
            unpack_actor_effects(pending[0])
        except ValueError:
            return None
        if self.world.actor_effects.get(refid) == pending[0]:
            return RELAY
        if not pending[0]:
            self.world.actor_effects.pop(refid, None)
        elif refid in self.world.actor_effects or len(self.world.actor_effects) < ACTOR_EFFECT_ACTORS:
            self.world.actor_effects[refid] = pending[0]
        self.world.dirty = True
        if self.detail["verbose"]:
            print(f"{stamp} client {client.id}: {refid:#010x} "
                  f"{len(pending[0]) // PLAYER_EFFECT_BYTES} active effects", flush=True)
        return RELAY

    def event_actor_move(self, client, kind, data, stamp, now):
        """Keep an actor's latest move with the cell it first left, and relay it in that form;
        a cell move drops its kept state, which the next report from its new cell replaces."""
        refid, origin, cell, x, y, z, heading = ACTOR_MOVE.unpack(data)
        kept = self.world.moves.get(refid)
        if kept is None and len(self.world.moves) >= ACTOR_MOVES_MAX:
            return KEEP
        if kept and kept[1] == cell and math.dist(kept[2:5], (x, y, z)) < 1 and \
                abs(kept[5] - heading) < 0.5:
            return KEEP
        move = [kept[0] if kept else origin, cell, x, y, z, heading]
        self.world.moves[refid] = move
        if origin != cell:
            self.world.actors.pop(refid, None)
        self.world.dirty = True
        if self.detail["verbose"]:
            print(f"{stamp} client {client.id}: {refid:#010x} moved to cell {cell} "
                  f"({x:.0f}, {y:.0f}, {z:.0f})", flush=True)
        self.broadcast_event(client.id, kind, ACTOR_MOVE.pack(refid, *move), now)
        return KEEP

    def event_ai_package(self, client, kind, data, stamp, now):
        """Keep an actor's latest AI package, a player target by character, and send it to
        every other console as that console names its target."""
        refid, ai_kind, target, cell, other, config, reset, ident = unpack_ai_package(data)
        if target == AI_TARGET_OBJECT:
            target = ["object", ident]
        elif target:
            named = client if target == AI_TARGET_PLAYER else (
                None if other == BOT_ID else self.clients.get(other))
            if other == BOT_ID and target == AI_TARGET_CLIENT:
                target = ["bot"]
            elif named is None or not named.character:
                print(f"{stamp} client {client.id}: {refid:#010x} AI package for a player with "
                      "no character, not kept", flush=True)
                return KEEP
            else:
                target = ["player", named.character]
        else:
            target = None
        if refid not in self.world.ai and len(self.world.ai) >= AI_PACKAGES_MAX:
            return KEEP
        entry = {"kind": ai_kind, "cell": cell, "config": config.hex(), "reset": reset,
                 "target": target}
        self.world.ai[refid] = entry
        self.world.dirty = True
        if self.detail["verbose"]:
            print(f"{stamp} client {client.id}: {refid:#010x} AI package {ai_kind}"
                  + (f" for {target[-1]}" if target else ""), flush=True)
        for recipient in self.clients.values():
            if recipient.in_world and recipient.id != client.id:
                self.send_ai_package(recipient, refid, entry, now)
        return KEEP

    def replay_records(self, client):
        """Every kept record, whole, before the character's items and spells that name them."""
        for blob in self.world.records.values():
            for part in record_parts(bytes.fromhex(blob)):
                client.rel.queue(EVENT_RECORD, 0, part)

    def event_record(self, client, kind, data, stamp, now):
        """Collect a console's record from its parts, keep it, and send it whole to the others."""
        part, parts = data[0], data[1]
        if part == 0:
            client.record_in = [parts, 0, bytearray()]
        pending = client.record_in
        if (pending is None or pending[0] != parts or pending[1] != part
                or parts > RECORD_PARTS_MAX):
            client.record_in = None
            return KEEP
        pending[2] += data[2:]
        pending[1] += 1
        if pending[1] < parts:
            return KEEP
        client.record_in = None
        blob = bytes(pending[2])
        try:
            ident = unpack_record(blob)[4]
        except ValueError as error:
            print(f"{stamp} client {client.id}: bad record ({error})", flush=True)
            return KEEP
        key = ident.lower()
        if key in self.world.records:
            return KEEP
        if len(self.world.records) >= RECORDS_MAX:
            print(f"{stamp} client {client.id}: record {ident} not kept, table full", flush=True)
            return KEEP
        self.world.records[key] = blob.hex()
        self.world.dirty = True
        if self.detail["verbose"]:
            print(f"{stamp} client {client.id}: record {ident}", flush=True)
        for other in self.clients.values():
            if other.in_world and other.id != client.id:
                for chunk in record_parts(blob):
                    other.rel.queue(EVENT_RECORD, client.id, chunk)
                self.flush(other, now)
        return KEEP

    def follow_bot(self, refid, now):
        """As AIFollow from the bot's dialogue: refid follows the bot's ghost."""
        config = struct.pack("<3fB3x", AI_NO_DESTINATION, AI_NO_DESTINATION, 0.0, 0)
        entry = {"kind": AI_FOLLOW, "cell": NO_CELL, "config": config.hex(), "reset": 1,
                 "target": ["bot"]}
        self.world.ai[refid] = entry
        self.world.dirty = True
        for recipient in self.clients.values():
            if recipient.in_world:
                self.send_ai_package(recipient, refid, entry, now)

    def ai_package_for(self, recipient, refid, entry):
        """The package as recipient names its target, or None while that player is away."""
        target, other, ident = entry["target"], 0, ""
        if target is None:
            named = 0
        elif target[0] == "object":
            named, ident = AI_TARGET_OBJECT, target[1]
        elif target[0] == "bot":
            named, other = AI_TARGET_CLIENT, BOT_ID
        elif recipient.character == target[1]:
            named = AI_TARGET_PLAYER
        else:
            present = [c for c in self.clients.values()
                       if c.in_world and c.character == target[1]]
            if not present:
                return None
            named, other = AI_TARGET_CLIENT, present[0].id
        return pack_ai_package(refid, entry["kind"], named, entry["cell"], other,
                               bytes.fromhex(entry["config"]), entry["reset"], ident)

    def send_ai_package(self, recipient, refid, entry, now):
        data = self.ai_package_for(recipient, refid, entry)
        if data is not None:
            recipient.rel.queue(EVENT_AI_PACKAGE, 0, data)
            self.flush(recipient, now)

    def world_script(self, client, op, refid, name, values, now):
        """Keep a running world script with its first runner's locals; it ends when the last
        console running it stops it."""
        key = name.lower()
        kept = self.world.scripts.get(key)
        runners = self.script_runners.setdefault(key, [])
        if op == SCRIPT_STOPPED:
            if client.id in runners:
                runners.remove(client.id)
            if kept is None or runners:
                return None
            del self.world.scripts[key]
            self.world.dirty = True
            return f"ended script {name}"
        if kept is None:
            if len(self.world.scripts) >= WORLD_SCRIPTS_MAX:
                return None
            kept = self.world.scripts[key] = {"id": name, "ref": refid, "values": {}}
            self.world.dirty = True
        if client.id not in runners:
            runners.append(client.id)
        if runners[0] != client.id or not values:
            return f"runs script {name}" if op == SCRIPT_RUNNING else None
        room = SCRIPT_VALUES_MAX - len(kept["values"])
        changed = {}
        for slot, value in values.items():
            if kept["values"].get(slot) == value or slot not in kept["values"] and room <= 0:
                continue
            room -= slot not in kept["values"]
            changed[slot] = value
        if not changed:
            return None
        kept["values"].update(changed)
        self.world.dirty = True
        return f"script {name} " + ", ".join(describe_local(*v) for v in sorted(changed.items()))

    def scripts_assign(self, client):
        """WORLD_SCRIPTS events handing the world scripts nobody runs to this client."""
        events = []
        for key, kept in sorted(self.world.scripts.items()):
            runners = self.script_runners.setdefault(key, [])
            if runners:
                continue
            runners.append(client.id)
            if kept["values"]:
                events += pack_world_script(SCRIPT_VALUES, kept["ref"], kept["id"], kept["values"])
            events += pack_world_script(SCRIPT_RUNNING, kept["ref"], kept["id"])
            print(f"{time.strftime('%H:%M:%S')} client {client.id} runs script {kept['id']}"
                  f" ({len(kept['values'])} locals)", flush=True)
        return events

    def scripts_orphaned(self, client_id, now):
        """A client that leaves stops running its world scripts; the lowest client still playing
        takes the ones nobody else runs."""
        orphaned = False
        for runners in self.script_runners.values():
            if client_id in runners:
                runners.remove(client_id)
                orphaned |= not runners
        if not orphaned:
            return
        for other in sorted(self.clients.values(), key=lambda c: c.id):
            if other.id != client_id and other.alive and other.in_world and other.synced:
                for data in self.scripts_assign(other):
                    other.rel.queue(EVENT_PLAYER, 0, data)
                self.flush(other, now)
                return

    def event_text(self, client, kind, data, stamp, now):
        """A player's line, shown as a server notice under their name: consoles put only the
        server's text on screen. A private one goes to one player, or only to the admin."""
        target = None
        if data[:1] == bytes([TEXT_PRIVATE]):
            target, _, data = data[1:].partition(b"\0")
            target = " ".join(wire_text(target).split())
        text = " ".join(wire_text(data).split())
        if not text or target == "":
            return KEEP
        name = self.player_name(client)
        if target is None:
            print(f"{stamp} client {client.id} ({name}) says: {text}", flush=True)
            self.notify(f"{name}: {text}", now)
            return KEEP
        if target.lower() == "admin":
            print(f"{stamp} client {client.id} ({name}) to admin: {text}", flush=True)
            self.notify(f"You to Admin: {text}", now, only=client)
            return KEEP
        players = [other for other in self.clients.values() if other.in_world]
        found = [other for other in players if self.player_name(other).lower() == target.lower()]
        if not found:
            found = [other for other in players
                     if self.player_name(other).lower().startswith(target.lower())]
        if len(found) != 1:
            self.notify(f"No player is called {target}." if not found else
                        f"{len(found)} players' names start with {target}.", now, only=client)
            return KEEP
        other = found[0]
        print(f"{stamp} client {client.id} ({name}) to client {other.id} "
              f"({self.player_name(other)}): {text}", flush=True)
        self.notify(f"{name} to you: {text}", now, only=other)
        if other is not client:
            self.notify(f"You to {self.player_name(other)}: {text}", now, only=client)
        return KEEP

    def event_game(self, client, kind, data, stamp, now):
        if len(data) < 5:
            return RELAY
        loaded, _, rest = data[4:].partition(b"\0")
        self.on_game(client, struct.unpack_from("<I", data)[0], wire_text(loaded),
                     rest[0] if rest else GAME_NONE, stamp, now)
        return KEEP

    def event_pick(self, client, kind, data, stamp, now):
        if len(data) < 2:
            return RELAY
        self.on_pick(client, data[0], data[1], stamp, now)
        return KEEP

    def event_busy(self, client, kind, data, stamp, now):
        if not data:
            return RELAY
        if data[0] and client.busy is None:
            client.busy = now
            print(f"{stamp} client {client.id} is saving: its cells and actors go to others",
                  flush=True)
        elif not data[0] and client.busy is not None:
            print(f"{stamp} client {client.id} is back after {now - client.busy:.1f} s",
                  flush=True)
            client.busy = None
        return RELAY

    def event_bounty(self, client, kind, data, stamp, now):
        if len(data) < 4:
            return RELAY
        bounty = struct.unpack_from("<i", data)[0]
        self.bounties[client.id] = data[:4]
        stream = self.player_stream(client) if client.synced else None
        if stream and stream.bounty != bounty:
            stream.bounty, stream.dirty = bounty, True
        print(f"{stamp} client {client.id} bounty {bounty}",
              flush=True)
        return RELAY

    def event_rest(self, client, kind, data, stamp, now):
        """A console's rest menu opening or closing, or a rest asked for: opening the menu is
        agreeing, and the rest starts once every console in the world has it open."""
        op = data[0]
        if op == REST_OPEN:
            self.rest_menus.add(client.id)
        elif op == REST_CLOSE:
            self.rest_menus.discard(client.id)
            if self.rest_by == client.id:
                self.rest_cancel(client, now)
        elif op == REST_ASK:
            self.rest_menus.add(client.id)
            if self.rest_hours is None:
                self.rest_hours, self.rest_by = struct.unpack_from("<H", data, 1)[0], client.id
                name = self.player_name(client)
                print(f"{stamp} client {client.id} ({name}) asks to rest {self.rest_hours} hours",
                      flush=True)
                waiting = [c for c in self.clients.values()
                           if c.in_world and c.id not in self.rest_menus]
                for other in waiting:
                    self.notify(f"{name} wants to rest for {self.rest_hours} hours. Open your "
                                "rest menu to agree.", now, only=other)
                if waiting:
                    self.notify("Waiting for " + ", ".join(self.player_name(c) for c in waiting)
                                + " to open their rest menu.", now, only=client)
        self.rest_check(now)
        return KEEP

    def rest_cancel(self, client, now):
        """The player who asked for a rest left or closed the menu: the request goes."""
        self.rest_hours = self.rest_by = None
        self.notify(f"{self.player_name(client)} is not resting.", now, skip=client)

    def rest_check(self, now):
        """Start the rest asked for once every console in the world has its menu open: the
        server's clock moves on by the hours, and each console runs its own wait or rest."""
        members = [c for c in self.clients.values() if c.in_world]
        self.rest_menus &= {c.id for c in members}
        if self.rest_hours is None or not members or len(self.rest_menus) < len(members):
            return
        hours = self.rest_hours
        self.rest_hours = self.rest_by = None
        self.rest_menus.clear()
        if self.world.clock:
            self.world.clock.advance(now)
            self.world.clock.skip(hours)
        print(f"{time.strftime('%H:%M:%S')} rest {hours} hours for {len(members)} "
              f"client(s), clock {self.world.clock}", flush=True)
        for member in members:
            member.rel.queue(EVENT_REST, 0, struct.pack("<BH", REST_START, hours))
            self.flush(member, now)

    def event_equipment(self, client, kind, data, stamp, now):
        if len(data) < 2:
            return RELAY
        sets = self.equipment.setdefault(client.id, [[], []])
        if data[0] == 0:
            sets[1] = []
        sets[1].append(data)
        if data[0] + 1 == data[1]:
            sets[0], sets[1] = sets[1], []
            items = [i for part in sets[0] for i in unpack_equipment(part)]
            print(f"{stamp} client {client.id} wears {len(items)}: {', '.join(items)}",
                  flush=True)
        return RELAY

    def event_identity(self, client, kind, data, stamp, now):
        try:
            part, female, first, second = unpack_identity(data)
        except ValueError:
            return KEEP
        parts = self.identities.get(client.id)
        if part == 0:
            parts = self.identities[client.id] = [data, None]
        elif not parts or not parts[0] or bool(parts[0][2]) != female:
            return KEEP
        else:
            parts[1] = data
        if parts[1]:
            name, race = unpack_identity(parts[0])[2:]
            head, hair = unpack_identity(parts[1])[2:]
            print(f"{stamp} client {client.id} is {name}: {race}, {head}, {hair}",
                  flush=True)
            if client.announce_due:
                self.announce_join(client, time.monotonic())
        return RELAY

    def event_actor_equipment(self, client, kind, data, stamp, now):
        try:
            refid, part, count, items = unpack_actor_equipment(data)
        except ValueError:
            return KEEP
        have = self.actor_equipment.get(refid)
        if part == 0:
            have = self.actor_equipment[refid] = [client.id, have[1] if have else [], []]
        elif not have or have[0] != client.id or len(have[2]) != part or \
                have[2][0][5] != count:
            return KEEP
        have[2].append(data)
        if part + 1 == count:
            have[1], have[2] = have[2], []
            worn = [item for body in have[1] for item in unpack_actor_equipment(body)[3]]
            print(f"{stamp} client {client.id}: {refid:#010x} wears {len(worn)}: "
                  f"{', '.join(worn)}", flush=True)
        return RELAY

    EVENT_HANDLERS = {EVENT_OFFER: event_offer,
                      EVENT_PLAYER: event_player,
                      EVENT_SNAPSHOT: event_snapshot,
                      EVENT_CONTENTS: event_contents,
                      EVENT_WANT: event_want,
                      EVENT_SPAWN: event_spawn,
                      EVENT_REMOVE: event_remove,
                      EVENT_WEATHER: event_weather,
                      EVENT_SPELL: event_spell,
                      EVENT_CAST: event_cast,
                      EVENT_SHOT: event_shot,
                      **dict.fromkeys(TARGETED, event_targeted),
                      EVENT_DEATH: event_death,
                      EVENT_KILL: event_kill,
                      EVENT_STATUS: event_status,
                      EVENT_AFFECT: event_affect,
                      EVENT_OBJECTS: event_objects,
                      EVENT_LOCALS: event_locals,
                      EVENT_ACTOR_STATS: event_actor_stats,
                      EVENT_ACTOR_EFFECTS: event_actor_effects,
                      EVENT_TEXT: event_text,
                      EVENT_GAME: event_game,
                      EVENT_PICK: event_pick,
                      EVENT_BUSY: event_busy,
                      EVENT_BOUNTY: event_bounty,
                      EVENT_REST: event_rest,
                      EVENT_EQUIPMENT: event_equipment,
                      EVENT_IDENTITY: event_identity,
                      EVENT_ACTOR_EQUIPMENT: event_actor_equipment,
                      EVENT_ACTOR_MOVE: event_actor_move,
                      EVENT_AI_PACKAGE: event_ai_package,
                      EVENT_RECORD: event_record}

    def update_authority(self, now):
        """Name each loaded cell's authority and tell every client that has the cell loaded."""
        candidates, standing = {}, set()
        for client in sorted(self.clients.values(), key=lambda c: c.id):
            client.loaded = set()
            if client.alive and client.state:
                own, client.loaded = cell_keys(client.state)
                standing.add(own)
                for key in client.loaded:
                    candidates.setdefault(key, []).append((client.id, key == own))
        busy = {c.id for c in self.clients.values() if c.busy is not None}
        for key, cands in candidates.items():
            if any(c not in busy for c, _ in cands):
                candidates[key] = [c for c in cands if c[0] not in busy]
        forced = None
        if self.bot.state and self.bot.window(self.args.bot_owns, now):
            own, loaded = cell_keys(self.bot.state)
            for key in loaded:
                candidates.setdefault(key, []).append((BOT_ID, key == own))
            forced = BOT_ID
        new = assign_authority(self.owners, candidates, forced)
        stamp = time.strftime("%H:%M:%S")
        for key in sorted(standing & set(new), key=describe_key):  # the rest only follow
            if self.owners.get(key) != new[key]:
                print(f"{stamp} authority {describe_key(key)}: client {new[key]}", flush=True)
        previous = dict(self.owners)
        self.owners.clear()
        self.owners.update(new)
        for client in self.clients.values():
            told = False
            for key in client.loaded:
                if key in new and client.known.get(key) != new[key]:
                    client.known[key] = new[key]
                    if new[key] == client.id and key not in previous:
                        places = self.actor_places(key)
                        for data in places:
                            client.rel.queue(EVENT_ACTOR_PLACE, 0, data)
                        if places and self.detail["verbose"]:
                            print(f"{stamp} client {client.id}: kept actors of "
                                  f"{describe_key(key)} sent", flush=True)
                    client.rel.queue(EVENT_AUTHORITY, 0, KEY.pack(*key) + struct.pack("<I", new[key]))
                    told = True
            for key in [k for k in client.known if k not in client.loaded]:
                del client.known[key]
            if told:
                self.flush(client, now)
        self.update_owners(now, forced)

    def update_owners(self, now, forced):
        """Name each actor's owner by proximity and tell every client that loads its cell where
        that differs from the cell's authority."""
        players = {}
        for client in self.clients.values():
            if client.alive and client.state and client.loaded and client.busy is None:
                players[client.id] = (client.loaded,) + STATE_BODY.unpack_from(client.state)[1:3]
        if self.args.bot_at and self.bot.state and not self.bot.busy:
            players[BOT_ID] = (cell_keys(self.bot.state)[1],) + \
                STATE_BODY.unpack_from(self.bot.state)[1:3]
        for refid in [r for r, seen in self.actor_seen.items() if now - seen > OWNER_STALE]:
            del self.actor_seen[refid]
        live = {}
        for refid in self.actor_seen:
            spawn = self.world.spawns.get(refid)
            if spawn and spawn.get("summon"):
                continue  # run by its maker
            _, key, record = self.actors[refid]
            values = ACTOR.unpack(record)
            live[refid] = (key, values[1], values[2], values[6], values[9])
        new = {} if forced else assign_owners(self.actor_owners, live, players, self.owners, now)
        stamp = time.strftime("%H:%M:%S")
        for refid, (client_id, _) in new.items():
            if self.actor_owners.get(refid, (None,))[0] not in (None, client_id):
                print(f"{stamp} actor {refid:#010x} owned by client {client_id}", flush=True)
        self.actor_owners.clear()
        self.actor_owners.update(new)
        for client in self.clients.values():
            if not client.alive:
                continue
            changes = []
            for refid, (client_id, _) in new.items():
                want = client_id if client_id != self.owners.get(live[refid][0]) else 0
                if live[refid][0] in client.loaded and client.owners_told.get(refid, 0) != want:
                    changes.append((refid, want))
            for refid, told in client.owners_told.items():
                if told and (refid not in new or live[refid][0] not in client.loaded):
                    changes.append((refid, 0))
            for refid, want in changes:
                if want:
                    client.owners_told[refid] = want
                else:
                    client.owners_told.pop(refid, None)
            for i in range(0, len(changes), OWNERS_PER_EVENT):
                chunk = changes[i:i + OWNERS_PER_EVENT]
                client.rel.queue(EVENT_OWNERS, 0, struct.pack("<I", len(chunk)) +
                                 b"".join(OWNER_PAIR.pack(*c) for c in chunk))
            if changes:
                self.flush(client, now)

    def actor_authority(self, refid):
        """The authority of the cell an actor was last reported in, or None."""
        return self.owners.get(self.actors[refid][1]) if refid in self.actors else None

    def on_actors(self, client, body):
        """Keep an authority's actor states and relay them to the other clients."""
        count = struct.unpack_from("<I", body)[0]
        own = cell_keys(client.state)[0] if client.state else None
        kept = []  # records with a finite position and statistics, the only ones relayed
        for i in range(min(count, (len(body) - 4) // ACTOR.size)):
            record = body[4 + i * ACTOR.size:4 + (i + 1) * ACTOR.size]
            values = ACTOR.unpack(record)
            if not placeable(*values[1:5]) or not finite(values[5], *values[7:9]):
                continue
            refid, x, y = values[:3]
            key = own if own and own[0] == KEY_INTERIOR else (
                KEY_EXTERIOR, math.floor(x / CELL_UNITS), math.floor(y / CELL_UNITS), b"")
            self.actors[refid] = (client.id, key, record)
            self.actor_seen[refid] = time.monotonic()
            self.keep_actor(refid, key, values)
            if refid == self.args.bot_mirror:
                self.bot.mirror = record
            client.actor_states += 1
            kept.append(record)
        if not kept:
            return
        body = struct.pack("<I", len(kept)) + b"".join(kept)
        for other in self.clients.values():
            if other is not client and other.in_world:
                self.send(other, ACTORS, struct.pack("<I", client.id) + body)

    def keep_actor(self, refid, key, values):
        """Keep an actor's latest state for the next authority of its cell; summons are made
        again by their effects."""
        kept = self.world.actors
        spawn = self.world.spawns.get(refid)
        if spawn and spawn.get("summon") or refid not in kept and len(kept) >= ACTORS_KEPT_MAX:
            return
        kept[refid] = [key[0], key[1], key[2], key[3].decode("latin-1"), *values[1:6],
                       values[7], values[8], values[6]]
        self.world.dirty = True

    def actor_places(self, key):
        """ACTOR_PLACE events for the kept actors last reported in a cell."""
        cell = [key[0], key[1], key[2], key[3].decode("latin-1")]
        records = []
        for refid, kept in sorted(self.world.actors.items()):
            spawn = self.world.spawns.get(refid)
            if kept[:4] == cell and not (spawn and spawn["removed"]):
                records.append(ACTOR_PLACE.pack(refid, *kept[4:11], int(kept[11])))
        return [struct.pack("<I", len(chunk)) + b"".join(chunk)
                for chunk in (records[i:i + ACTOR_PLACES_PER_EVENT]
                              for i in range(0, len(records), ACTOR_PLACES_PER_EVENT))]

    def handshake(self, kind, session, packet, addr, now, version):
        """Answer HANDSHAKE1 with HANDSHAKE2; on HANDSHAKE3, the HELLO it carries, the console's
        key, the session keys and whether this HANDSHAKE3 came before."""
        for stale in [k for k, v in self.pending.items() if now - v["time"] > HANDSHAKE_KEEP]:
            del self.pending[stale]
        entry = self.pending.get(session)
        if entry and entry["version"] != version:
            return None
        if kind == HANDSHAKE1:
            e = packet[OUTER.size:OUTER.size + 32]
            if len(packet) < HANDSHAKE_PAD or entry and entry["e"] != e:
                return None
            if entry is None:
                noise = Noise(False, self.server_secret, os.urandom(32), PROLOGUE)
                noise.read1(e)
                entry = self.pending[session] = {
                    "noise": noise, "e": e, "time": now, "done": None, "version": version,
                    "reply": OUTER.pack(b"T3MP", version, HANDSHAKE2, 0, session, 0)
                    + noise.write2()}
            self.transmit(addr, entry["reply"])
            return None
        if kind != HANDSHAKE3 or entry is None:
            return None
        message = packet[OUTER.size:]
        if entry["done"] is None:
            try:
                hello = entry["noise"].read3(message)
            except ValueError:
                return None
            entry["done"] = (message, hello, entry["noise"].rs, entry["noise"].split())
            return entry["done"][1:] + (False,)
        if entry["done"][0] != message:
            return None
        return entry["done"][1:] + (True,)

    def guarded(self, packet, addr):
        """handle, with a packet that breaks a parser dropped and logged instead of ending the
        server: the parsers check lengths and ranges, this is the second line."""
        try:
            self.handle(packet, addr)
        except (struct.error, ValueError, IndexError, KeyError, TypeError, OverflowError) as error:
            where = traceback.extract_tb(error.__traceback__)[-1]
            print(f"{time.strftime('%H:%M:%S')} malformed packet from {addr[0]}: "
                  f"{type(error).__name__} in {where.name}: {error}", flush=True)

    def handle(self, packet, addr):
        """Take a handshake message or open a sealed packet, then hand it on in the T3MP
        layout. Anything else is dropped unread."""
        if len(packet) < OUTER.size or self.dropped("in") or addr[0] in self.bans["address"]:
            return
        magic, version, kind, _, session, seq = OUTER.unpack_from(packet)
        if magic != b"T3MP" or not version:
            return
        now = time.monotonic()
        if kind in (HANDSHAKE1, HANDSHAKE3):
            if kind == HANDSHAKE1 and session not in self.pending:
                source = self.handshake_buckets.get(addr[0], now)
                if (len(self.pending) >= HANDSHAKES_PENDING or source is None or not source.take(now)
                        or not self.handshake_bucket.take(now)):
                    self.limits["handshakes"] += 1
                    return
            done = self.handshake(kind, session, packet, addr, now, version)
            if done:
                hello, key, keys, again = done
                if len(hello) < 18:
                    return
                dash = bool(struct.unpack_from("<I", hello, 14)[0] & MANAGER)
                expected = MANAGER_VERSION if dash else T3MP_VERSION
                if version != expected:
                    stranger = self.handshake_client(addr, session, keys, hello[:6].hex(":"),
                                                     version)
                    self.send(stranger, REFUSE,
                              REFUSE_BODY.pack(expected, version, REFUSED_PROTOCOL))
                    print(f"{time.strftime('%H:%M:%S')} refused "
                          f"{'Dash' if dash else 'game'} at {addr[0]}: protocol "
                          f"{version}, expected {expected}", flush=True)
                    return
                client = self.by_session.get(session)
                if again and client is not None and client.keys == keys:
                    # The WELCOME was lost, or this is a replay: answer the address that joined.
                    self.send(client, WELCOME, struct.pack("<I", client.id))
                    return
                self.handle_plain(T3MP.pack(b"T3MP", version, HELLO, 0, session, 0, 0, 0, 0)
                                  + hello, addr, (key, keys))
            return
        client = self.by_session.get(session)
        if version != T3MP_VERSION or kind != SEALED or client is None or client.keys is None or \
                len(packet) < OUTER.size + INNER.size + NOISE_TAG:
            return
        top, seen = client.replay
        if not seq or seq <= top and (top - seq >= 32 or seen >> (top - seq) & 1):
            client.replayed += 1
            return
        inner = unseal(client.keys[0], seq, packet[:OUTER.size], packet[OUTER.size:])
        if inner is None:
            client.forged += 1
            return
        if not client.bucket.take(now):
            client.limited += 1
            return
        if seq > top:
            client.replay = (seq, (seen << min(seq - top, 32) | 1) & 0xFFFFFFFF)
        else:
            client.replay = (top, seen | 1 << (top - seq))
        inner_kind, ack, sent, echo = INNER.unpack_from(inner)
        self.handle_plain(T3MP.pack(b"T3MP", T3MP_VERSION, inner_kind, 0, session, seq, ack, sent,
                                    echo) + inner[INNER.size:], addr)

    def handshake_client(self, addr, session, keys, mac, version):
        # Repeated HANDSHAKE3 replies need distinct nonces and the original destination.
        entry = self.pending[session]
        if "reply_client" not in entry:
            client = entry["reply_client"] = Client(0, mac)
            client.addr, client.session, client.keys = addr, session, keys
            client.version = version
        return entry["reply_client"]

    def refuse(self, addr, session, keys, mac, reason, version=T3MP_VERSION):
        stranger = self.handshake_client(addr, session, keys, mac, version)
        order, plugins = self.pinned or (0, 0)
        self.send(stranger, REFUSE, REFUSE_BODY.pack(order, plugins or 0, reason))

    def kick(self, client, reason):
        """Refuse a joined client, which stops it until the game is launched again."""
        if client.alive and client.keys:
            order, plugins = self.pinned or (0, 0)
            self.send(client, REFUSE, REFUSE_BODY.pack(order, plugins or 0, reason))
        if client.alive:
            self.leave(client)
        self.by_session.pop(client.session, None)

    def ask_save(self, targets, now, diagnostic=False):
        for client in targets:
            if client.alive:
                client.snapshot_request = 0x80000000 | ((client.snapshot_request + 1) & 0x7fffffff)
                client.rel.queue(EVENT_SAVE, 0, b"\x01" if diagnostic else
                                 struct.pack("<I", client.snapshot_request))
                self.flush(client, now)
        return ", ".join(f"client {c.id}" for c in targets if c.alive) or "nobody"

    def admin(self, line):
        """Run an admin command; the reply to print."""
        words = line.split()
        verb, rest = (words[0].lower(), words[1:]) if words else ("", [])
        by_id = {str(c.id): c for c in self.clients.values()}
        if verb == "list" and not rest:
            return "\n".join(
                f"client {c.id}: {'playing' if c.alive else 'away'}, key "
                f"{fingerprint(c.key) if c.key else '-'}, mac {c.mac}, address "
                f"{c.addr[0] if c.addr else '-'}"
                + (f", name {self.listed_name(c)}" if self.listed_name(c) else "")
                for c in sorted(self.clients.values(), key=lambda c: c.id)) or "no clients"
        if verb == "kick" and len(rest) == 1 and rest[0] in by_id:
            self.kick(by_id[rest[0]], REFUSED_KICKED)
            return f"kicked client {rest[0]}"
        if verb == "ban" and len(rest) == 1 and rest[0] in by_id:
            client = by_id[rest[0]]
            rest = ["key", fingerprint(client.key), "mac", client.mac]
        if verb in ("ban", "unban") and rest and len(rest) % 2 == 0:
            pairs = [(kind, ban_value(kind, value)) for kind, value in zip(rest[::2], rest[1::2])]
            if all(value for _, value in pairs):
                for kind, value in pairs:
                    (self.bans[kind].add if verb == "ban" else self.bans[kind].discard)(value)
                save_bans(self.bans_path, self.bans)
                if verb == "ban":
                    for c in list(self.clients.values()):
                        if c.alive and ((c.key and fingerprint(c.key) in self.bans["key"]) or
                                        c.mac in self.bans["mac"] or
                                        (c.addr and c.addr[0] in self.bans["address"])):
                            self.kick(c, REFUSED_BANNED)
                return (f"{verb}ned " if verb == "ban" else "unbanned ") + ", ".join(
                    f"{kind} {value}" for kind, value in pairs) + (
                    "" if self.bans_path else " (not kept: give --world)")
        if verb == "save" and len(rest) <= 1 and all(r in by_id for r in rest):
            targets = [by_id[r] for r in rest] or list(self.clients.values())
            return "asked to save: " + self.ask_save(targets, time.monotonic())
        if verb == "log" and len(rest) <= 1 and (not rest or rest[0] in LOG_LEVELS):
            if rest:
                self.detail["verbose"] = rest[0] == "verbose"
            return "log " + ("verbose" if self.detail["verbose"] else "normal")
        if verb == "say" and rest:
            self.notify("Admin: " + " ".join(rest), time.monotonic())
            return "sent to everyone"
        if verb == "tell" and len(rest) >= 2 and rest[0] in by_id:
            self.notify("Admin: " + " ".join(rest[1:]), time.monotonic(), only=by_id[rest[0]])
            return f"sent to client {rest[0]}"
        if verb == "stop" and not rest:
            self.begin_stop("admin stop", time.monotonic())
            return "stopping"
        if verb == "bans" and not rest:
            return "\n".join(f"{kind} {value}" for kind in BAN_KINDS
                             for value in sorted(self.bans[kind])) or "no bans"
        return ("commands:\n"
                "  list                  the clients\n"
                "  status                the clock, weather, clients and world\n"
                "  kick N                drop client N\n"
                "  log [normal|verbose]  show or set whether state changes (skills, vitals,\n"
                "                        statuses, objects) are printed\n"
                "  save [N]              ask every console, or client N, to save\n"
                "  ban N                 ban client N's key and MAC, and drop it\n"
                "  ban|unban key FINGERPRINT|mac MAC|address A.B.C.D\n"
                "  bans                  the bans\n"
                "  say TEXT              show TEXT on every console\n"
                "  tell N TEXT           show TEXT on client N's console\n"
                "  stop                  save every character, then stop the server")

    def status(self, now):
        """The server's state, one line each."""
        out = []
        if self.world.clock:
            self.world.clock.advance(now)
            out.append(f"  clock {self.world.clock}")
        if self.world.weather:
            out.append(f"  weather: {describe_weather(self.world.weather)}")
        if self.limits["handshakes"]:
            out.append(f"  handshakes refused over rate: {self.limits['handshakes']}")
        for client in self.clients.values():
            out.append(f"  client {client.id}: {'up' if client.alive else 'down'}"
                  + (" (saving)" if client.busy is not None else "") + ", "
                  + summary(client))
        if self.world.objects:
            out.append(f"  objects: {len(self.world.objects)} changed")
        if self.world.spawns:
            live = sum(not s["removed"] for s in self.world.spawns.values())
            out.append(f"  spawns: {live} live, {len(self.world.spawns) - live} removed")
        if self.actors:
            out.append(f"  actors: {len(self.actors)} known; authorities "
                  + ", ".join(f"{describe_key(k)} {c}" for k, c in sorted(
                      self.owners.items(), key=lambda i: describe_key(i[0]))))
            runs = {}
            for client_id, _ in self.actor_owners.values():
                runs[client_id] = runs.get(client_id, 0) + 1
            out.append("  actors run by: " + ", ".join(
                f"client {c} {n}" for c, n in sorted(runs.items())))
        return "\n".join(out)

    def required_build(self):
        """The (build ID, revision) a joining console must have, or None. A served build's revision
        is the digest of the manifest it hands out, which its Dash install records."""
        if self.content_source:
            return (bytes.fromhex(self.content_source.build),
                    bytes.fromhex(self.content_source.revision))
        served = self.build_server.build_id() if self.build_server else None
        return (served, self.build_server.build.sha256) if served else None

    def handle_plain(self, packet, addr, secure=None):
        magic, version, kind, _, session, seq, _, sent, _ = T3MP.unpack_from(packet)
        stamp = time.strftime("%H:%M:%S")
        now = time.monotonic()
        if kind == HELLO and secure and len(packet) >= T3MP.size + HELLO_BODY.size:
            self.hello(packet, addr, secure, version, session, seq, sent, stamp, now)
            return
        client = self.by_session.get(session)
        if client is None:
            return
        if seq > client.peer_seq + 1:
            client.gaps += seq - client.peer_seq - 1
        client.peer_seq = max(client.peer_seq, seq)
        client.peer_time, client.addr, client.last = sent, addr, time.monotonic()
        if not client.alive:
            print(f"{stamp} client {client.id} back", flush=True)
            client.alive = True
        handler = self.PACKET_HANDLERS.get(kind)
        if handler:
            handler(self, client, packet, session, stamp, now)

    def hello(self, packet, addr, secure, version, session, seq, sent, stamp, now):
        """A HELLO sealed by a finished handshake: admit, refuse or rejoin the console, and
        send a joining one the shared world."""
        key, keys = secure
        mac, build, order, plugins, *offered, build_id, revision, selection = \
            HELLO_BODY.unpack_from(packet, T3MP.size)
        dash = bool(plugins & MANAGER)
        mac, lobby, plugins = mac.hex(":"), bool(plugins & LOBBY), plugins & ~LOBBY & ~MANAGER
        if fingerprint(key) in self.bans["key"] or mac in self.bans["mac"]:
            print(f"{stamp} refused {mac} at {addr[0]}: banned", flush=True)
            self.refuse(addr, session, keys, mac, REFUSED_BANNED, version)
            return
        if self.password and key not in self.admitted:
            tries = self.password_buckets.get(addr[0], now)
            given = packet[T3MP.size + HELLO_BODY.size:]
            if tries is None or not tries.take(now) or not self.password.check(given):
                print(f"{stamp} refused {mac} at {addr[0]}: wrong password", flush=True)
                self.refuse(addr, session, keys, mac, REFUSED_PASSWORD, version)
                return
            self.admitted.add(key)
            if self.admitted_path:
                with open(self.admitted_path, "a", encoding="utf-8") as stream:
                    stream.write(f"{key.hex()} {mac} {time.strftime('%Y-%m-%d')}\n")
            print(f"{stamp} admitted key {fingerprint(key)} ({mac})", flush=True)
        if dash:
            stranger = self.handshake_client(addr, session, keys, mac, version)
            body = self.build_server.ticket() if self.build_server else \
                tes3x_netbuild.BUILD_BODY.pack(bytes(32), 0, 0, bytes(16))
            who = self.dash_character(key).encode("latin-1", "replace")[:46]
            playing = sum(c.alive and not c.lobby for c in self.clients.values())
            info = tes3x_netbuild.dash_info(self.args.name, self.args.description,
                                               playing, self.args.max_players)
            source = self.content_source.block(revision) if self.content_source else b""
            self.send(stranger, BUILD, body + who + b'\0' + info + source)
            print(f"{stamp} Dash {fingerprint(key)} at {addr[0]} asked for the build"
                  + (", served by its content library" if self.content_source
                     else "" if self.build_server else ", which is not served"), flush=True)
            return
        required = self.required_build() if any(build_id) else None
        if required and build_id != required[0]:
            print(f"{stamp} refused {mac}: build {build_id.hex()[:16]}, the server's is "
                  f"{required[0].hex()[:16]}", flush=True)
            self.refuse(addr, session, keys, mac, REFUSED_STALE)
            return
        if required and any(revision) and revision != required[1]:
            print(f"{stamp} refused {mac}: revision {revision.hex()[:16]}, the server's is "
                  f"{required[1].hex()[:16]}", flush=True)
            self.refuse(addr, session, keys, mac, REFUSED_STALE)
            return
        if required and any(selection):
            # until patches say which must match, any local selection is another build
            print(f"{stamp} refused {mac}: a local patch selection "
                  f"{selection.hex()[:16]}", flush=True)
            self.refuse(addr, session, keys, mac, REFUSED_STALE)
            return
        if self.pinned is None and not lobby:
            self.pinned = (order, plugins)
            print(f"{stamp} load order {order:#010x} ({plugins} plugins) set by {mac}",
                  flush=True)
            self.adopt_world(order, now)
        if self.pinned and order != self.pinned[0]:
            print(f"{stamp} refused {mac}: load order {order:#010x} ({plugins} plugins), "
                  f"session has {self.pinned[0]:#010x}", flush=True)
            self.refuse(addr, session, keys, mac, REFUSED_LOAD_ORDER)
            return
        # A console is known by its key for this server; the MAC is only a hint.
        client = self.clients.get(key)
        playing = sum(c.alive for c in self.clients.values() if c is not client)
        if playing >= self.args.max_players:
            print(f"{stamp} refused {mac}: {playing} players, the most allowed", flush=True)
            self.refuse(addr, session, keys, mac, REFUSED_FULL)
            return
        if client is None:
            client = self.clients[key] = Client(len(self.clients) + 1, mac)
            client.key = key
            print(f"{stamp} client {client.id} is key {fingerprint(key)}", flush=True)
        client.mac = mac
        self.by_session.pop(client.session, None)
        client.session = session
        self.by_session[client.session] = client
        if client.keys != keys:  # a resent HANDSHAKE3 keeps the replay window
            client.keys, client.replay = keys, (0, 0)
        client.addr, client.peer_seq, client.peer_time = addr, seq, sent
        client.joins += 1
        client.alive, client.last = True, now
        if client.rel.out:
            print(f"{stamp} client {client.id}: {len(client.rel.out)} unacked events "
                  f"dropped by the rejoin", flush=True)
        client.rel = Reliable()
        client.known = {}
        client.owners_told = {}
        client.busy = None
        client.lobby = lobby
        if client.joins == 1:
            client.joined = now
            client.bursts = sorted(self.bursts)
        verb = "joined" if client.joins == 1 else "rejoined"
        print(f"{stamp} client {client.id} {verb}: {mac} at {addr[0]}:{addr[1]}, "
              f"build {build:#010x}", flush=True)
        self.send(client, WELCOME, struct.pack("<I", client.id))
        if lobby:
            print(f"{stamp} client {client.id} is at the main menu", flush=True)
            return
        if self.world.clock is None:
            offered = sane_clock(offered)
            if self.args.hour is not None:
                offered[0] = self.args.hour
            if self.args.timescale is not None:
                offered[5] = self.args.timescale
            self.world.clock = Clock(*offered, now)
            print(f"{stamp} clock {self.world.clock}, from client {client.id}", flush=True)
        self.send(client, CLOCK, self.world.clock.body(now))
        client.rel.queue(EVENT_RULES, 0, bytes([self.rules(), self.combat_leave()]))
        for refid, origin in self.world.deaths.items():
            client.rel.queue(EVENT_DEATH, origin, struct.pack("<I", refid))
        for data in pack_objects(self.world.objects):
            client.rel.queue(EVENT_OBJECTS, 0, data)
        for refid, values in sorted(self.world.locals.items()):
            for data in pack_locals(refid, values):
                client.rel.queue(EVENT_LOCALS, 0, data)
        for name, count in sorted(self.kill_counts().items()):
            client.rel.queue(EVENT_KILLS, 0, pack_kills(name, count))
        for refid, values in sorted(self.world.actor_stats.items()):
            for data in pack_actor_stats(refid, values):
                client.rel.queue(EVENT_ACTOR_STATS, 0, data)
        for refid, body in sorted(self.world.actor_effects.items()):
            for data in pack_actor_effects(refid, body):
                client.rel.queue(EVENT_ACTOR_EFFECTS, 0, data)
        for refid, move in sorted(self.world.moves.items()):
            client.rel.queue(EVENT_ACTOR_MOVE, 0, ACTOR_MOVE.pack(refid, *move))
        for refid, values in self.world.statuses.items():
            client.rel.queue(EVENT_STATUS, 0, STATUS.pack(refid, *values))
        for sid, spawn in sorted(self.world.spawns.items(), key=lambda s: s[1]["removed"]):
            client.rel.queue(EVENT_SPAWN, spawn["origin"] if spawn.get("summon") else 0,
                             pack_spawn(sid, spawn))
        for origin, (parts, _) in self.equipment.items():
            if origin != client.id:
                for part in parts:
                    client.rel.queue(EVENT_EQUIPMENT, origin, part)
        for origin, parts in self.identities.items():
            if origin != client.id and all(parts):
                for part in parts:
                    client.rel.queue(EVENT_IDENTITY, origin, part)
        for origin, parts, _ in self.actor_equipment.values():
            for part in parts:
                client.rel.queue(EVENT_ACTOR_EQUIPMENT, origin, part)
        for data in pack_weather(self.world.weather):
            client.rel.queue(EVENT_WEATHER, 0, data)
        for origin, data in self.bounties.items():
            if origin != client.id:
                client.rel.queue(EVENT_BOUNTY, origin, data)
        for other in self.clients.values():
            if other is not client and other.in_world and other.dead:
                client.rel.queue(EVENT_PLAYER, other.id, bytes([PLAYER_DEATH]))
        if self.sending and (client.bulk is None or client.bulk.status != 3):
            client.bulk = Outgoing(*self.sending)
            client.rel.queue(EVENT_OFFER, 0, client.bulk.offer())
            print(f"{stamp} offering {self.sending[0]} ({len(self.sending[1])} bytes, id "
                  f"{client.bulk.id:#010x}) to client {client.id}", flush=True)
        if not self.key_folder(client):  # with characters, it joins once it has one
            self.announce_join(client, now)
        if client.rel.out:
            self.flush(client, now)

    def packet_heartbeat(self, client, packet, session, stamp, now):
        client.beats += 1
        self.send(client, HEARTBEAT)

    def packet_state(self, client, packet, session, stamp, now):
        if len(packet) < T3MP.size + STATE_SIZE:
            return
        if not placeable(*STATE_BODY.unpack_from(packet, T3MP.size)[1:5]):
            return
        client.state = packet[T3MP.size:T3MP.size + STATE_SIZE]
        client.states += 1
        self.keep_place(client, now)
        if self.args.bot:
            self.bot.follow(client.state)
            if self.args.bot_echo:
                self.bot.echo = client.state
        for other in self.clients.values():
            if other is not client and other.in_world:
                self.send(other, PEER, struct.pack("<I", client.id) + client.state)

    def packet_actors(self, client, packet, session, stamp, now):
        if len(packet) < T3MP.size + 4:
            return
        self.on_actors(client, packet[T3MP.size:])

    def packet_bulk_ack(self, client, packet, session, stamp, now):
        if not client.bulk or len(packet) < T3MP.size + BULK_ACK_BODY.size:
            return
        bulk = client.bulk
        first = bulk.first is None
        status = bulk.on_ack(packet[T3MP.size:], now)
        if first and bulk.first is not None:
            print(f"{stamp} client {client.id} takes {bulk.name} from chunk {bulk.first} "
                  f"of {bulk.chunks}", flush=True)
        if status == BULK_NO_SPACE:
            print(f"{stamp} client {client.id} has no room for {bulk.name} "
                  f"({len(bulk.data)} bytes and its margin)", flush=True)
        elif status is not None and status != BULK_RECEIVING:
            took = now - bulk.started
            size = len(bulk.data) - min(bulk.first, bulk.chunks) * BULK_CHUNK
            print(f"{stamp} client {client.id} {bulk.name}: "
                  f"{BULK_STATUS[status] if status < len(BULK_STATUS) else status}, "
                  f"{max(size, 0)} bytes in {took:.1f} s "
                  f"({max(size, 0) / 1024 / max(took, 0.001):.0f} KB/s), "
                  f"chunks sent {bulk.sent}, resent {bulk.resent} ({bulk.fast} on a gap), "
                  f"probes {bulk.probes}",
                  flush=True)

    def packet_chunk(self, client, packet, session, stamp, now):
        if not client.upload or len(packet) < T3MP.size + 8:
            return
        upload = client.upload
        ident, index = struct.unpack_from("<II", packet, T3MP.size)
        if ident != upload.id:
            return
        receiving = upload.status == BULK_RECEIVING
        if upload.on_chunk(index, packet[T3MP.size + 8:]):
            self.send(client, BULK_ACK, upload.ack(now))
        if receiving and upload.status != BULK_RECEIVING:
            took = max(now - upload.started, 0.001)
            size = upload.size - upload.first * BULK_CHUNK
            print(f"{stamp} client {client.id} sent {upload.name}: "
                  f"{BULK_STATUS[upload.status]}, {size} bytes in {took:.1f} s "
                  f"({size / 1024 / took:.0f} KB/s)", flush=True)
            if upload.status == BULK_DONE:
                self.received(client, stamp, now)

    def packet_events(self, client, packet, session, stamp, now):
        if len(packet) < T3MP.size + EVENTS_HEAD.size:
            return
        ready, carried = client.rel.receive(packet[T3MP.size:])
        if carried:
            self.flush(client, now, resend=False)
        for _, event_kind, _, data in ready:
            self.on_event(client, event_kind, data, stamp, now)

    def packet_bye(self, client, packet, session, stamp, now):
        print(f"{stamp} client {client.id} left", flush=True)
        self.leave(client)
        self.by_session.pop(session, None)

    PACKET_HANDLERS = {HEARTBEAT: packet_heartbeat,
                       STATE: packet_state,
                       ACTORS: packet_actors,
                       BULK_ACK: packet_bulk_ack,
                       CHUNK: packet_chunk,
                       EVENTS: packet_events,
                       BYE: packet_bye}


    def begin_stop(self, why, now):
        if self.stop["until"] is not None:
            return
        self.stop["until"] = now + self.args.stop_wait
        self.notify("The server is shutting down.", now)
        asked = self.ask_save([c for c in self.clients.values() if c.alive], now)
        self.stop["waiting"] = {c.id: c.snapshot_request for c in self.clients.values()
                                if c.alive and c.synced}
        print(f"{time.strftime('%H:%M:%S')} stopping ({why}): asked to save: {asked}; waiting "
              f"up to {self.args.stop_wait:g} s for "
              + (", ".join(f"client {i}" for i in self.stop["waiting"]) or "nobody"), flush=True)
        signal.signal(signal.SIGINT, signal.default_int_handler)

    def stopped(self, now):
        if self.stop["until"] is None:
            if self.deadline is None or now < self.deadline:
                return False
            self.begin_stop("duration over", now)
        for ident, kept in list(self.stop["waiting"].items()):
            client = next(c for c in self.clients.values() if c.id == ident)
            if client.snapshot_saved == kept or not client.alive:
                del self.stop["waiting"][ident]
                print(f"{time.strftime('%H:%M:%S')} client {ident} "
                      + ("saved" if client.snapshot_saved == kept else "left without saving"), flush=True)
        if self.stop["waiting"] and now < self.stop["until"]:
            return False
        for ident in self.stop["waiting"]:
            print(f"{time.strftime('%H:%M:%S')} client {ident} did not save in "
                  f"{self.args.stop_wait:g} s", flush=True)
        return True

    def run(self):
        sys.stdout.reconfigure(errors="replace")  # the console's code page cannot print every name
        self.links = [Tunnel(port) for port in self.args.tunnel]
        self.sock = udp_socket()
        self.sock.bind((self.args.bind, self.args.port))
        print(f"serving on {self.args.bind}:{self.args.port}"
              + "".join(f" and tunnel {port}" for port in self.args.tunnel), flush=True)
        self.clients, self.by_session = {}, {}
        self.hosts = {}
        for entry in self.args.host:
            name, _, address = entry.partition("=")
            socket.inet_aton(address)
            self.hosts[name.lower().rstrip(".")] = address
        self.dns = None
        if self.hosts:
            self.dns = udp_socket()
            self.dns.bind((self.args.bind, DNS_PORT))
            print(f"answering DNS on {self.args.bind}:{DNS_PORT} for "
                  f"{', '.join(sorted(self.hosts))}",
                  flush=True)
        self.deadline = time.monotonic() + self.args.duration if self.args.duration else None
        self.report_next = time.monotonic() + self.args.report
        self.loss = random.Random(self.args.seed)
        self.pinned = None
        if self.args.load_order:
            self.pinned = (int(self.args.load_order, 16), None)
        self.lost = {"in": 0, "out": 0}
        self.clock_next = 0.0
        self.rest_menus = set()  # clients with their rest menu open
        self.rest_hours = self.rest_by = None  # a rest asked for, and by whom
        self.save_next = (time.monotonic() + self.args.save_every if self.args.save_every
                          else math.inf)
        self.owners = {}  # cell -> authority client
        self.actor_owners = {}  # actor id -> (client, since): its owner by proximity
        self.actor_seen = {}  # actor id -> when a state of it last came
        self.dialogues = {}  # actor id -> (talking client, authority client)
        # world script id lowered -> clients running it, the first one's locals kept
        self.script_runners = {}
        # client -> [parts of its latest whole equipment set, parts of the set arriving]
        self.equipment = {}
        self.identities = {}  # client -> its complete [name/race, head/hair] parts
        self.actor_equipment = {}  # actor id -> (authority, complete parts, arriving parts)
        self.bounties = {}
        # refid -> (reporting client, cell, ACTOR bytes), the latest from an authority
        self.actors = {}
        self.arriving = {}  # client id -> (refid, entries so far, next part)
        self.bot = Bot(self, time.monotonic())
        self.world = World()
        # per-tick state changes in the console
        self.detail = {"verbose": self.args.log == "verbose"}
        self.streams = {}  # character folder -> PlayerStream
        self.streams_saved = 0.0
        self.starts = load_starts(self.args.starts or STARTS)
        self.creating = set()  # key fingerprints making a new character
        self.authority_next = 0.0
        self.server_secret = load_server_key(self.args)
        self.password = load_password(self.args)
        self.admitted_path = (os.path.join(self.args.world, "admitted.txt") if self.args.world
                              else None)
        self.admitted = load_admitted(self.admitted_path)
        self.password_buckets = SourceBuckets(*PASSWORD_RATE)
        self.bans_path = os.path.join(self.args.world, "bans.txt") if self.args.world else None
        self.bans = load_bans(self.bans_path)
        self.admin_sock, commands = None, queue.Queue()
        admin_port = ADMIN_PORT if self.args.admin_port is None else self.args.admin_port
        if admin_port:
            self.admin_sock = udp_socket()
            try:
                self.admin_sock.bind(("127.0.0.1", admin_port))
                print(f"admin commands on 127.0.0.1:{admin_port} (tes3x net admin)"
                      + (", and here" if sys.stdin and sys.stdin.isatty() else ""), flush=True)
            except OSError as error:
                self.admin_sock = None
                print(f"no admin port: 127.0.0.1:{admin_port}: {error}", flush=True)
        self.remote_admin = None
        if self.args.remote_admin:
            password_path = self.args.admin_password_file or (
                os.path.join(self.args.world, "admin-password.txt") if self.args.world else None)
            if not password_path or not os.path.isfile(password_path):
                sys.exit("--remote-admin needs --admin-password-file, or admin-password.txt in "
                         "--world")
            remote_sock = udp_socket()
            remote_sock.bind((self.args.bind, self.args.remote_admin))
            self.remote_admin = RemoteAdmin(load_admin_secret(password_path), remote_sock)
            print(f"remote admin on {self.args.bind}:{self.args.remote_admin}, with the password "
                  f"in "
                  f"{password_path}", flush=True)
        if sys.stdin and sys.stdin.isatty():
            threading.Thread(target=lambda: [commands.put(line) for line in sys.stdin],
                             daemon=True).start()
        if self.password:
            print(f"password asked of new consoles; {len(self.admitted)} admitted"
                  + ("" if self.admitted_path else " (not kept: give --world)"), flush=True)
        self.handshake_bucket = Bucket(*HANDSHAKE_RATE_ALL)
        self.handshake_buckets = SourceBuckets(*HANDSHAKE_RATE)
        self.limits = {"handshakes": 0}
        # session -> a handshake in progress or just done: {"noise", "e", "reply", ...}
        self.pending = {}
        self.bursts = [(float(at), int(count)) for count, _, at in
                       (spec.partition("@") for spec in self.args.burst)]
        self.build_server = None
        self.content_source = None
        content_options = (self.args.content_source or self.args.content_key
                           or self.args.content_entry or self.args.content_state)
        if content_options and self.args.build:
            sys.exit("--build and a content source are alternatives: choose one")
        if content_options:
            from tes3x import content as tes3x_content
            if not self.args.content_entry:
                sys.exit("a content source needs --content-entry")
            key_path = self.args.content_delegation_key or (
                os.path.join(self.args.world, "content-delegation.key") if self.args.world
                else None)
            if not key_path:
                sys.exit("a content source needs --world or --content-delegation-key")
            try:
                self.content_source = tes3x_content.WorldSource.configure(
                    tes3x_content.delegation_identity(key_path), self.args.content_source,
                    self.args.content_key, self.args.content_entry, self.args.content_build,
                    self.args.content_revision, self.args.content_state,
                    self.args.name or os.path.basename(os.path.normpath(self.args.world or "")))
            except (OSError, ValueError) as error:
                sys.exit(f"content source: {error}")
            source = self.content_source
            print(f"content source {source.host or '(this host)'}:{source.port} "
                  f"{source.entry} revision {source.revision[:16]}; delegation key "
                  f"{source.delegate}"
                  + ("" if self.args.content_state else
                     " (the library must trust it: tes3x content delegate)"), flush=True)
        if self.args.build:
            retail_base = self.args.retail_base
            if not retail_base:
                import tomllib
                from tes3x.paths import local_config
                config_path = local_config()
                if config_path.is_file():
                    with config_path.open('rb') as stream:
                        retail_base = tomllib.load(stream).get('paths', {}).get('vanilla_root')
                    if retail_base:
                        retail_base = config_path.parent / retail_base
            build = tes3x_netbuild.Build(self.args.build, self.args.deltas,
                                         self.args.serve_origin or tes3x_netbuild.SERVED_BY_DEFAULT,
                                         retail_base=retail_base)
            self.build_server = tes3x_netbuild.BuildServer(
                build, self.args.bind,
                self.args.port if self.args.http_port is None else self.args.http_port)
            print(f"{build.describe()}; HTTP on {self.args.bind}:{self.build_server.port}",
                  flush=True)
        self.sending = None
        if self.args.send:
            name = os.path.basename(self.args.send)
            if not plain_name(name):
                raise SystemExit(f"--send: {name!r} is not a plain name of at most {BULK_NAME} "
                                 "characters")
            with open(self.args.send, "rb") as stream:
                self.sending = (name, stream.read())

        # Stopping asks every joined console for its character and waits, up to --stop-wait, for the
        # saves of those running one; a second Ctrl-C stops at once.
        self.stop = {"until": None, "waiting": {}}

        previous = signal.signal(signal.SIGINT, lambda *_: commands.put("stop"))
        try:
            if self.pinned:
                self.adopt_world(self.pinned[0], time.monotonic())
            while not self.stopped(time.monotonic()):
                while not commands.empty():
                    print(self.admin(commands.get()), flush=True)
                waiting = ([self.sock] + [link.sock for link in self.links]
                           + [link.forward for link in self.links]
                           + ([self.dns] if self.dns else [])
                           + ([self.admin_sock] if self.admin_sock else [])
                           + ([self.remote_admin.sock] if self.remote_admin else []))
                wait = 0.25
                if any(c.queue for c in self.clients.values()):
                    wait = PACE_WINDOW
                elif any(c.flush_due for c in self.clients.values()):
                    wait = EVENTS_GAP
                elif any(c.bulk and c.bulk.status == BULK_RECEIVING for c in self.clients.values()):
                    wait = 0.05
                for ready in select.select(waiting, [], [], wait)[0]:
                    self.receive(ready)
                self.tick(time.monotonic())
            if self.world.path:
                self.world.save(time.monotonic())
            for stream in self.streams.values():
                if stream.dirty:
                    stream.save()
            for client in self.clients.values():
                print(f"client {client.id} {client.mac}: " + summary(client, "last "))
            if self.args.drop:
                print(f"dropped {self.lost['in']} packets in, {self.lost['out']} out")
            return 0
        finally:
            if previous is not None:
                signal.signal(signal.SIGINT, previous)

    def receive(self, ready):
        """Handle one socket select found readable."""
        if ready is self.admin_sock:
            try:
                line, addr = self.admin_sock.recvfrom(2048)
            except ConnectionResetError:
                return
            reply = self.admin(line.decode("utf-8", "replace"))
            if not quiet_admin(line):
                print(f"{time.strftime('%H:%M:%S')} admin: {wire_text(line)}: {reply}",
                      flush=True)
            self.admin_sock.sendto(reply.encode("utf-8"), addr)
            return
        if self.remote_admin and ready is self.remote_admin.sock:
            try:
                data, addr = self.remote_admin.sock.recvfrom(2048)
            except ConnectionResetError:
                return

            def run(line, addr=addr):
                reply = self.admin(line)
                if not quiet_admin(line.encode("utf-8")):
                    print(f"{time.strftime('%H:%M:%S')} remote admin from {addr[0]}: "
                          f"{wire_text(line.encode('utf-8'))}: {reply}", flush=True)
                return reply

            reply = self.remote_admin.handle(data, addr, run)
            if reply:
                self.remote_admin.sock.sendto(reply, addr)
            return
        if ready is self.dns:
            try:
                query, addr = self.dns.recvfrom(2048)
            except ConnectionResetError:
                return
            reply = dns_reply(query, self.hosts)
            if reply:
                print(f"{time.strftime('%H:%M:%S')} dns query from {addr[0]}: "
                      f"{'answered' if reply[7] else 'unknown name'}", flush=True)
                self.dns.sendto(reply, addr)
            return
        if ready is self.sock:
            try:
                data, addr = self.sock.recvfrom(2048)
            except ConnectionResetError:
                return
            self.guarded(data, addr)
            return
        forwarding = next((link for link in self.links if link.forward is ready), None)
        if forwarding:
            try:
                data, address = forwarding.forward.recvfrom(2048)
            except ConnectionResetError:
                return
            guest = forwarding.forward_guest.get(address[1])
            if guest:
                mac, guest_ip, guest_port = guest
                host_port = address[1]
                forwarding.send(udp_frame(mac, guest_ip, data, sport=host_port,
                                          dport=guest_port))
            return
        link = next(link for link in self.links if link.sock is ready)
        frame = link.recv(0)
        if frame and frame[12:14] == b"\x08\x06" and len(frame) >= 42:
            op, sha, spa, _, tpa = struct.unpack_from(">H6s4s6s4s", frame, 20)
            if op == 1 and tpa == socket.inet_aton(PEER_IP):
                link.send(arp_frame(2, sha, sha, socket.inet_ntoa(spa)))
        elif frame:
            src = socket.inet_ntoa(frame[26:30])
            request = udp_from_frame(frame, DHCP_SERVER)
            reply = dhcp_reply(request, self.args.dhcp_lease) if request else None
            if reply:
                print(f"{time.strftime('%H:%M:%S')} dhcp "
                      f"{'offer' if reply[242] == 2 else 'ack'} {GUEST_IP} to "
                      f"{frame[6:12].hex(':')}", flush=True)
                link.send(udp_frame(frame[6:12], "255.255.255.255", reply,
                                    sport=DHCP_SERVER, dport=DHCP_CLIENT))
                return
            query = udp_from_frame(frame, DNS_PORT) if self.hosts else None
            reply = dns_reply(query, self.hosts) if query else None
            if reply:
                print(f"{time.strftime('%H:%M:%S')} dns query from {src}: "
                      f"{'answered' if reply[7] else 'unknown name'}", flush=True)
                link.send(udp_frame(frame[6:12], src, reply, sport=DNS_PORT))
                return
            udp = 14 + (frame[14] & 0x0F) * 4 if len(frame) >= 42 else 0
            sport = (struct.unpack_from(">H", frame, udp)[0] if udp and frame[23] == 17
                     else 0)
            data = udp_from_frame(frame, self.args.port)
            if data:
                self.guarded(data, (src, sport, frame[6:12], link))
                return
            if udp and frame[23] == 17:
                sport, dport = struct.unpack_from(">HH", frame, udp)
                data = udp_from_frame(frame, dport)
                if data is not None and dport in self.args.forward_ports:
                    link.forward_guest[dport] = (frame[6:12], src, sport)
                    link.forward.sendto(data, ("127.0.0.1", dport))

    def tick(self, now):
        """What the loop does on time rather than on a packet, once per pass."""
        for client in self.clients.values():
            if client.queue:
                self.pump(client)
            if client.alive and client.in_world and client.announce_due:
                self.announce_join(client, now)
            if client.alive and now - client.last > self.args.idle_timeout:
                print(f"{time.strftime('%H:%M:%S')} client {client.id} timed out", flush=True)
                self.leave(client)
        if self.world.path and \
                now >= self.world.saved + (10 if self.world.dirty else 60):
            self.world.save(now)
        if now >= self.streams_saved + 2:
            self.streams_saved = now
            for stream in self.streams.values():
                if stream.dirty:
                    stream.save()
        self.bot.move(now)
        if now >= self.authority_next:
            self.authority_next = now + AUTHORITY_PERIOD
            self.update_authority(now)
        self.bot.act(now)
        self.rest_check(now)
        for client in [c for c in self.clients.values() if c.alive and c.bursts]:
            if now - client.joined >= client.bursts[0][0]:
                _, count = client.bursts.pop(0)
                print(f"{time.strftime('%H:%M:%S')} burst of {count} heartbeats to client "
                      f"{client.id}", flush=True)
                for _ in range(count):
                    self.send(client, HEARTBEAT)
                    client.queue, queued = [], client.queue
                    for addr, packet, seq in queued:
                        self.transmit(addr, packet, seq)
        for client in [c for c in self.clients.values() if c.alive and c.bulk]:
            for index in client.bulk.due(now):
                self.send(client, CHUNK, client.bulk.chunk(index))
        for client in [c for c in self.clients.values() if c.alive and c.upload]:
            if client.upload.status == BULK_RECEIVING and \
                    now - client.upload.acked >= BULK_ACK_EVERY:
                self.send(client, BULK_ACK, client.upload.ack(now))
        for client in self.clients.values():
            if client.alive and (client.flush_due or client.rel.out and
                                 now - client.rel.last_send >= RESEND):
                self.flush(client, now)
        for client in self.clients.values():
            if client.alive and client.rebuild and client.rebuild[1] and \
                    now >= client.rebuild[1]:
                client.rebuild = (client.rebuild[0], None)
                print(f"{time.strftime('%H:%M:%S')} asked client {client.id} for its rebuilt "
                      f"character: {self.ask_save([client], now, diagnostic=True)}", flush=True)
        if now >= self.save_next:
            self.save_next = now + self.args.save_every
            if any(c.alive for c in self.clients.values()):
                print(f"{time.strftime('%H:%M:%S')} asked to save: "
                      f"{self.ask_save(list(self.clients.values()), now, diagnostic=self.args.adopt)}",
                      flush=True)
        if self.world.clock and now >= self.clock_next:
            self.clock_next = now + CLOCK_INTERVAL
            body = self.world.clock.body(now)
            for client in self.clients.values():
                if client.alive and not client.lobby:
                    self.send(client, CLOCK, body)
        if self.args.report and now >= self.report_next:
            self.report_next = now + self.args.report
            print(self.status(now), flush=True)


def serve(args):
    return Server(args).run()
