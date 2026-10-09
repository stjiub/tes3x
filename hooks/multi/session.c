static void udp_send(u32 dst, u32 port, const u8 *payload, u32 n)
{
    tes3x_net_send(&multi_channel, dst, port, payload, n);
}

/* The session's keys from the handshake, and the replay window: the highest seq opened and a
 * bitmap of the 32 up to it. */
static struct {
    u32 keyed, top, seen, sealed, opened, forged, replayed;
    u8 send[NOISE_KEY], receive[NOISE_KEY];
} sec;

static void t3mp_outer(u8 *p, u32 type, u32 session, u32 seq)
{
    p[0] = 'T';
    p[1] = '3';
    p[2] = 'M';
    p[3] = 'P';
    p[4] = T3MP_VERSION;
    p[5] = (u8)type;
    p[6] = p[7] = 0;
    put32le(p + 8, session);
    put32le(p + 12, seq);
}

/* Caller holds the lock. Nothing is sent before the handshake has keyed the session. */
static void session_send(u32 type, const u8 *body, u32 n)
{
    static u8 inner[T3MP_INNER + SEND_BYTES], p[T3MP_OUTER + sizeof(inner) + NOISE_TAG];

    if (n > SEND_BYTES || !sec.keyed)
        return;
    t3mp_outer(p, T3MP_SEALED, ses.id, ++ses.seq);
    inner[0] = (u8)type;
    inner[1] = inner[2] = inner[3] = 0;
    put32le(inner + 4, ses.peer_seq);
    put32le(inner + 8, now_us());
    put32le(inner + 12, ses.peer_time);
    copy(inner + T3MP_INNER, body, n);
    noise_seal(sec.send, ses.seq, p, T3MP_OUTER, inner, T3MP_INNER + n, p + T3MP_OUTER);
    udp_send(ses.server, ses.port, p, T3MP_OUTER + T3MP_INNER + n + NOISE_TAG);
    sec.sealed++;
}

/* Latest state wins: one server sequences everything it sends us, so an older seq is stale. */
static void peer_rx(u32 client, u32 seq, const u8 *state)
{
    u32 i, slot = PEERS;

    if (!float_within(state + 4, 3, POSITION_LIMIT) || !float_within(state + 16, 1, ANGLE_LIMIT) ||
        !script_safe(state + 20, CELL_NAME)) {
        refused_states++;
        return;
    }
    for (i = 0; i < PEERS; i++) {
        if (peers[i].client == client) {
            slot = i;
            break;
        }
        if (slot == PEERS && !peers[i].client)
            slot = i;
    }
    if (slot == PEERS || (peers[slot].client == client && seq <= peers[slot].seq))
        return;
    if (peers[slot].client != client)
        peers[slot].count = peers[slot].busy = 0;
    peers[slot].client = client;
    peers[slot].seq = seq;
    peers[slot].time = now_us();
    peers[slot].samples[peers[slot].head].time = peers[slot].time;
    copy(peers[slot].samples[peers[slot].head].state, state, STATE_BYTES);
    peers[slot].head = (peers[slot].head + 1) % SAMPLES;
    if (peers[slot].count < SAMPLES)
        peers[slot].count++;
    ses.peers_in++;
}

static void events_reset(void)
{
    rel.dropped += rel.out_next - rel.out_first;
    rel.out_first = rel.out_next = rel.out_sent = 1;
    rel.in_next = 1;
    rel.in_count = 0;
}

/* The ack, then with resend every unacked event that fits; caller holds the lock. */
static void events_send(int resend)
{
    u8 body[EVENTS_BYTES];
    u32 n = 8, count = 0, seq;

    put32le(body, rel.in_next - 1);
    for (seq = rel.out_first; resend && seq != rel.out_next; seq++) {
        const struct event *e = &rel.out[seq % EVENTS_OUT];
        if (n + EVENT_HEADER + e->length > EVENTS_BYTES)
            break;
        put32le(body + n, e->seq);
        body[n + 4] = (u8)e->kind;
        body[n + 5] = (u8)(e->kind >> 8);
        body[n + 6] = (u8)e->length;
        body[n + 7] = (u8)(e->length >> 8);
        put32le(body + n + 8, 0);
        copy(body + n + EVENT_HEADER, e->data, e->length);
        n += EVENT_HEADER + e->length;
        count++;
        if ((int)(seq - rel.out_sent) < 0)
            rel.resent++;
        else
            rel.out_sent = seq + 1;
        rel.sent++;
    }
    body[4] = (u8)count;
    body[5] = body[6] = body[7] = 0;
    session_send(T3MP_EVENTS, body, n);
}

/* Game thread. 0 if the queue is full or there is no session. */
static int event_queue(u32 kind, const u8 *data, u32 length)
{
    u32 flags = lock();
    struct event *e;
    int ok = ses.state == SESSION_JOINED && rel.out_next - rel.out_first < EVENTS_OUT &&
             length <= EVENT_DATA;

    if (ok) {
        e = &rel.out[rel.out_next % EVENTS_OUT];
        e->seq = rel.out_next++;
        e->kind = kind;
        e->length = length;
        e->origin = 0;
        copy(e->data, data, length);
        rel.queued++;
        events_send(1);
    } else {
        rel.full++;
    }
    unlock(flags);
    return ok;
}

/* Caller holds the lock (the receive DPC). */
static void events_rx(const u8 *p, u32 n)
{
    u32 ack, count, off = 8, seq, length;
    struct event *e;

    if (n < 8)
        return;
    ack = get32le(p);
    while (rel.out_first != rel.out_next && (int)(ack - rel.out_first) >= 0) {
        rel.out_first++;
        rel.acked++;
    }
    count = p[4];
    if (!count)
        return;
    while (count-- && off + EVENT_HEADER <= n) {
        seq = get32le(p + off);
        length = p[off + 6] | (u32)p[off + 7] << 8;
        if (off + EVENT_HEADER + length > n)
            break;
        if (seq != rel.in_next || length > EVENT_DATA || rel.in_count == EVENTS_IN) {
            if ((int)(seq - rel.in_next) < 0)
                rel.stale++;
        } else {
            e = &rel.in[(rel.in_head + rel.in_count++) % EVENTS_IN];
            e->seq = seq;
            e->kind = p[off + 4] | (u32)p[off + 5] << 8;
            e->length = length;
            e->origin = get32le(p + off + 8);
            copy(e->data, p + off + EVENT_HEADER, length);
            rel.in_next++;
            rel.delivered++;
        }
        off += EVENT_HEADER + length;
    }
    events_send(0);
}

/* The date globals index tables and the engine rolls the hours over one day at a time, so a
 * CLOCK outside these (as float bits, none negative) is dropped. */
static const u32 clock_limits[CLOCK_GLOBALS][2] = {
    {0, 0x41C00000u},          /* GameHour 0-24 */
    {0x3F800000u, 0x41F80000u}, /* Day 1-31 */
    {0, 0x41300000u},          /* Month 0-11 */
    {0, 0x47C35000u},          /* Year to 100000 */
    {0, 0x4B189680u},          /* DaysPassed to 1e7 */
    {0, 0x461C4000u},          /* TimeScale to 10000 */
};

static int clock_sane(const u8 *p)
{
    u32 i, v;

    for (i = 0; i < CLOCK_GLOBALS; i++)
        if ((v = get32le(p + 4 * i)) < clock_limits[i][0] || v > clock_limits[i][1]) {
            refused_states++;
            return 0;
        }
    return 1;
}

/* Caller holds the lock (the receive DPC). p is an opened packet in the T3MP_HEADER layout. */
static void session_rx_plain(const u8 *p, u32 n)
{
    u32 type, seq, echo, rtt, i;

    if (n < T3MP_HEADER || p[4] != T3MP_VERSION)
        return;
    type = p[5];
    seq = get32le(p + 12);
    echo = get32le(p + 24);
    /* At join, or later as a kick or a ban. */
    if (type == T3MP_REFUSE) {
        if ((ses.state != SESSION_HELLO && ses.state != SESSION_JOINED) || n < T3MP_HEADER + 8)
            return;
        ses.state = SESSION_REFUSED;
        sec.keyed = 0;
        for (i = 0; i < PEERS; i++)
            peers[i].client = 0;
        ses.refused_hash = get32le(p + 28);
        ses.refused_plugins = get32le(p + 32);
        ses.refused_reason = n >= T3MP_HEADER + 12 ? get32le(p + 36) : 0;
        return;
    }
    if (type == T3MP_WELCOME) {
        if (ses.state != SESSION_HELLO || n < T3MP_HEADER + 4)
            return;
        ses.id = get32le(p + 8);
        ses.client = get32le(p + 28);
        ses.state = SESSION_JOINED;
        ses.ticks = 0;
        ses.peer_seq = seq;
        ses.welcomes++;
        handshake_reset();
        for (i = 0; i < PEERS; i++)
            peers[i].client = 0;
        events_reset();
        actors_reset();
    } else {
        if (ses.state != SESSION_JOINED || get32le(p + 8) != ses.id)
            return;
        if (type == T3MP_BYE) {
            ses.state = SESSION_HELLO;
            ses.ticks = HELLO_TICKS;
            sec.keyed = 0;
            return;
        }
        if (type == T3MP_HEARTBEAT)
            ses.beats_in++;
        if (type == T3MP_PEER && n >= T3MP_HEADER + 4 + STATE_BYTES)
            peer_rx(get32le(p + T3MP_HEADER), seq, p + T3MP_HEADER + 4);
        if (type == T3MP_EVENTS)
            events_rx(p + T3MP_HEADER, n - T3MP_HEADER);
        if (type == T3MP_CHUNK)
            bulk_chunk_rx(p + T3MP_HEADER, n - T3MP_HEADER);
        if (type == T3MP_BULK_ACK)
            up_ack_rx(p + T3MP_HEADER, n - T3MP_HEADER);
        if (type == T3MP_ACTORS && n >= T3MP_HEADER + 8)
            actors_rx(get32le(p + T3MP_HEADER), seq, p + T3MP_HEADER + 4, n - T3MP_HEADER - 4);
        if (type == T3MP_CLOCK && n >= T3MP_HEADER + CLOCK_BYTES && seq > ses.peer_seq &&
            clock_sane(p + T3MP_HEADER)) {
            copy(game_clock.server, p + T3MP_HEADER, CLOCK_BYTES);
            game_clock.received++;
        }
        if (type == T3MP_GONE && n >= T3MP_HEADER + 4)
            for (i = 0; i < PEERS; i++)
                if (peers[i].client == get32le(p + T3MP_HEADER))
                    peers[i].client = 0;
        if (seq > ses.peer_seq + 1)
            ses.gaps += seq - ses.peer_seq - 1;
        if (seq > ses.peer_seq)
            ses.peer_seq = seq;
    }
    ses.peer_time = get32le(p + 20);
    ses.quiet = 0;
    rtt = now_us() - echo;
    /* Only a heartbeat is answered at once; a relayed state echoes whatever we sent last. */
    if (type == T3MP_HEARTBEAT && echo && rtt < 10000000u) {
        ses.rtt_last = rtt;
        if (!ses.rtt_count || rtt < ses.rtt_min)
            ses.rtt_min = rtt;
        if (rtt > ses.rtt_max)
            ses.rtt_max = rtt;
        ses.rtt_sum += rtt;
        ses.rtt_count++;
    }
}

/* Caller holds the lock (the receive DPC). Only a handshake reply or a packet sealed under this
 * session's key and not seen before gets past here. */
static void session_rx(const u8 *p, u32 n)
{
    static u8 plain[12 + BUF];
    u32 seq, type, age;

    if (n < T3MP_OUTER || p[4] != T3MP_VERSION)
        return;
    if (p[5] == T3MP_HANDSHAKE2) {
        handshake_rx(p, n);
        return;
    }
    if (p[5] != T3MP_SEALED || !sec.keyed || get32le(p + 8) != ses.id ||
        n < T3MP_OUTER + T3MP_INNER + NOISE_TAG || n - T3MP_OUTER - NOISE_TAG > BUF)
        return;
    seq = get32le(p + 12);
    age = sec.top - seq;
    if (!seq || ((int)age >= 0 && (age >= 32 || sec.seen >> age & 1))) {
        sec.replayed++;
        return;
    }
    if (noise_open(sec.receive, seq, p, T3MP_OUTER, p + T3MP_OUTER, n - T3MP_OUTER, plain + 12)) {
        sec.forged++;
        return;
    }
    if ((int)age < 0) {
        sec.seen = -age >= 32 ? 1 : sec.seen << -age | 1;
        sec.top = seq;
    } else {
        sec.seen |= 1u << age;
    }
    sec.opened++;
    type = plain[12];
    t3mp_outer(plain, type, ses.id, seq);
    session_rx_plain(plain, 12 + n - T3MP_OUTER - NOISE_TAG);
}

/* Off the subnet, frames go to the gateway's MAC. Caller holds the lock. */
static void route_to(u32 target)
{
    tes3x_net_route(&multi_channel, target, ses.gateway);
    ses.state = SESSION_ARP;
    ses.ticks = 0;
}

/* An A query for ses.host; caller holds the lock. */
static void dns_query(void)
{
    u8 q[12 + HOST_NAME + 2 + 4];
    u32 n = 12, label = 12, i;

    ses.dns_id = (now_us() & 0xFFFF) | 1;
    for (i = 0; i < 12; i++)
        q[i] = 0;
    put16(q, ses.dns_id);
    q[2] = 0x01; /* recursion desired */
    q[5] = 1;    /* one question */
    for (i = 0; ses.host[i]; i++) {
        if (ses.host[i] == '.') {
            q[label] = (u8)(n - label);
            label = ++n;
        } else {
            q[++n] = (u8)ses.host[i];
        }
    }
    q[label] = (u8)(n - label);
    q[++n] = 0;
    put16(q + n + 1, 1); /* type A */
    put16(q + n + 3, 1); /* class IN */
    udp_send(ses.dns, DNS_PORT, q, n + 5);
    ses.dns_queries++;
}

/* Offset past a possibly compressed name, or 0 if it runs off the end. */
static u32 dns_skip_name(const u8 *p, u32 n, u32 off)
{
    while (off < n) {
        if (!p[off])
            return off + 1;
        if ((p[off] & 0xC0) == 0xC0)
            return off + 2 <= n ? off + 2 : 0;
        off += p[off] + 1u;
    }
    return 0;
}

/* The first A record of a reply to our query; caller holds the lock (the receive DPC). */
static void dns_rx(const u8 *p, u32 n)
{
    u32 off = 12, questions, answers;

    if (ses.state != SESSION_RESOLVE || n < 12 || get16(p) != ses.dns_id || !(p[2] & 0x80) ||
        (p[3] & 0x0F))
        return;
    questions = get16(p + 4);
    answers = get16(p + 6);
    while (questions--)
        if (!(off = dns_skip_name(p, n, off)) || (off += 4) > n)
            return;
    while (answers--) {
        if (!(off = dns_skip_name(p, n, off)) || off + 10 > n)
            return;
        if (get16(p + off) == 1 && get16(p + off + 2) == 1 && get16(p + off + 8) == 4 &&
            off + 14 <= n && get32(p + off + 10)) {
            ses.server = get32(p + off + 10);
            ses.dns_answers++;
            route_to(ses.server);
            return;
        }
        off += 10 + get16(p + off + 8);
    }
}

/* A DISCOVER or REQUEST, broadcast; caller holds the lock. A first REQUEST names the offer and
 * its server, a renewal the bound address. */
static void dhcp_send(u32 type)
{
    static const u8 options[] = {55, 4, 1, 3, 6, 51, 12, 5, 'T', 'E', 'S', '3', 'X'};
    u8 body[300], *b = body;
    u32 i, n = 240;

    for (i = 0; i < sizeof(body); i++)
        b[i] = 0;
    b[0] = 1; /* request, Ethernet, 6-byte address */
    b[1] = 1;
    b[2] = 6;
    put32(b + 4, dhcp.xid);
    put16(b + 10, 0x8000); /* reply by broadcast: there is no address to reply to yet */
    if (dhcp.state == DHCP_BOUND)
        put32(b + 12, net.ip);
    copy(b + 28, mac, 6);
    put32(b + 236, 0x63825363u);
    b[n++] = 53;
    b[n++] = 1;
    b[n++] = (u8)type;
    if (dhcp.state == DHCP_REQUEST) {
        b[n++] = 50;
        b[n++] = 4;
        put32(b + n, dhcp.offered);
        n += 4;
        b[n++] = 54;
        b[n++] = 4;
        put32(b + n, dhcp.server);
        n += 4;
    }
    copy(b + n, options, sizeof(options));
    n += sizeof(options);
    b[n] = 255;
    tes3x_net_send_broadcast(DHCP_CLIENT, DHCP_SERVER, body, sizeof(body));
}

/* Caller holds the lock. */
static void dhcp_discover(void)
{
    dhcp.state = DHCP_DISCOVER;
    dhcp.xid = now_us() ^ (u32)mac[4] << 24 ^ (u32)mac[5] << 16;
    dhcp.ticks = 0;
    dhcp_send(1);
    dhcp.discovers++;
}

/* The lease ended or was refused: the session waits for a new address. */
static void dhcp_lost(void)
{
    net.ip = 0;
    if (ses.state != SESSION_IDLE)
        ses.state = SESSION_DHCP;
    dhcp_discover();
}

static void dhcp_bind(u32 ip, u32 mask, u32 router, u32 dns, u32 lease)
{
    u32 fresh = ip != net.ip;

    net.ip = ip;
    net.mask = mask ? mask : 0xFFFFFF00u;
    if (!dhcp.keep_gateway)
        ses.gateway = router;
    tes3x_net_set_gateway(ses.gateway);
    if (!dhcp.keep_dns)
        ses.dns = dns ? dns : ses.gateway;
    if (!lease || lease > 7 * 86400u)
        lease = 7 * 86400u;
    dhcp.lease = (lease < 16 ? 16 : lease) * (1000 / TICK_MS);
    dhcp.state = DHCP_BOUND;
    dhcp.ticks = 0;
    if (!fresh)
        return;
    tes3x_net_arp(net.ip);
    if (ses.state == SESSION_DHCP) {
        if (ses.server || ses.host[0])
            route_to(ses.server ? ses.server : ses.dns);
        else
            ses.state = SESSION_IDLE;
    }
}

/* A reply to our transaction; caller holds the lock (the receive DPC). */
static void dhcp_rx(const u8 *p, u32 n)
{
    u32 off = 240, type = 0, mask = 0, router = 0, dns = 0, lease = 0, server = 0, i;

    if (n < 240 || p[0] != 2 || !dhcp.state || get32(p + 4) != dhcp.xid ||
        get32(p + 236) != 0x63825363u)
        return;
    for (i = 0; i < 6; i++)
        if (p[28 + i] != mac[i])
            return;
    while (off < n && p[off] != 255) {
        const u8 *v = p + off + 2;
        u32 code = p[off], len;

        if (!code) {
            off++;
            continue;
        }
        if (off + 2 > n || off + 2 + (len = p[off + 1]) > n)
            return;
        if (code == 53 && len >= 1)
            type = v[0];
        else if (len >= 4 && code == 1)
            mask = get32(v);
        else if (len >= 4 && code == 3)
            router = get32(v);
        else if (len >= 4 && code == 6)
            dns = get32(v);
        else if (len >= 4 && code == 51)
            lease = get32(v);
        else if (len >= 4 && code == 54)
            server = get32(v);
        off += 2 + len;
    }
    if (type == 2 && dhcp.state == DHCP_DISCOVER && get32(p + 16)) {
        dhcp.offers++;
        dhcp.offered = get32(p + 16);
        dhcp.server = server ? server : get32(p + 20);
        dhcp.state = DHCP_REQUEST;
        dhcp.ticks = dhcp.tries = 0;
        dhcp_send(3);
        dhcp.requests++;
        return;
    }
    if (dhcp.state == DHCP_DISCOVER || (server && dhcp.server && server != dhcp.server))
        return;
    if (type == 5 && get32(p + 16)) {
        dhcp.acks++;
        dhcp_bind(get32(p + 16), mask, router, dns, lease);
    } else if (type == 6) {
        dhcp.naks++;
        dhcp_lost();
    }
}

/* Every TICK_MS; caller holds the lock. Discovery every 2 s, a request every second (four, then
 * discovery again), and once bound a renewal every 4 s from half the lease on. */
static void dhcp_tick(void)
{
    dhcp.ticks++;
    if (dhcp.state == DHCP_DISCOVER && dhcp.ticks >= 8) {
        dhcp.ticks = 0;
        dhcp_send(1);
        dhcp.discovers++;
    } else if (dhcp.state == DHCP_REQUEST && dhcp.ticks >= 4) {
        dhcp.ticks = 0;
        if (++dhcp.tries >= 4) {
            dhcp_discover();
        } else {
            dhcp_send(3);
            dhcp.requests++;
        }
    } else if (dhcp.state == DHCP_BOUND) {
        if (dhcp.ticks >= dhcp.lease) {
            dhcp.expiries++;
            dhcp_lost();
        } else if (dhcp.ticks >= dhcp.lease / 2 && (dhcp.ticks - dhcp.lease / 2) % 16 == 0) {
            dhcp_send(3);
            dhcp.renewals++;
        }
    }
}

/* Every TICK_MS from the timer DPC; caller holds the lock. */
static void session_tick(void)
{
    ses.ticks++;
    ses.quiet++;
    if (ses.state == SESSION_ARP) {
        if (multi_channel.route.known) {
            ses.state = ses.server ? SESSION_HELLO : SESSION_RESOLVE;
            ses.ticks = HELLO_TICKS;
        } else if (ses.ticks % HELLO_TICKS == 1) {
            tes3x_net_arp(multi_channel.route.hop);
        }
    }
    if (ses.state == SESSION_RESOLVE && ses.ticks >= HELLO_TICKS) {
        ses.ticks = 0;
        dns_query();
    }
    /* Joining waits for a clock to offer, which the game has only once a game is loaded. HELLO
     * goes inside the handshake's last message. */
    if (ses.state == SESSION_HELLO && ses.ticks >= HELLO_TICKS &&
        (game_clock.local_valid || lobby)) {
        ses.ticks = 0;
        handshake_tick();
    } else if (ses.state == SESSION_JOINED) {
        if (rel.out_first != rel.out_next)
            events_send(1);
        bulk_tick();
        up_pump();
        if (ses.quiet >= TIMEOUT_TICKS) {
            ses.timeouts++;
            ses.state = SESSION_HELLO;
            ses.ticks = HELLO_TICKS - 1;
            sec.keyed = 0;
            if (ses.host[0]) { /* the name may point elsewhere now */
                ses.server = 0;
                route_to(ses.dns);
            }
        } else if (ses.ticks >= HEARTBEAT_TICKS) {
            ses.ticks = 0;
            session_send(T3MP_HEARTBEAT, 0, 0);
            ses.beats_out++;
        }
    }
}

static void multi_receive(u32 source, u32 port, const u8 *p, u32 n)
{
    if (port == DNS_PORT && ses.dns && source == ses.dns) {
        dns_rx(p, n);
    } else if (ses.state != SESSION_IDLE && source == ses.server && port == ses.port) {
        session_rx(p, n);
    }
}

static void dhcp_receive(u32 source, u32 port, const u8 *p, u32 n)
{
    (void)source;
    if (port == DHCP_SERVER)
        dhcp_rx(p, n);
}

static void multi_tick(void)
{
    entropy_add();
    if (net.up && dhcp.state)
        dhcp_tick();
    if (net.up && ses.state != SESSION_IDLE)
        session_tick();
}

static void stat(void)
{
    u32 i;

    tes3x_net_stat();
    if (dhcp.state) {
        tes3x_log_hex3("net.dhcp_stat", dhcp.discovers, dhcp.offers, dhcp.acks);
        tes3x_log_hex3("net.dhcp_lease", dhcp.state, dhcp.ticks / (1000 / TICK_MS),
                       dhcp.lease / (1000 / TICK_MS));
        tes3x_log_hex3("net.dhcp_lost", dhcp.naks, dhcp.expiries, dhcp.renewals);
    }
    if (ses.host[0]) {
        log_text("net.host", ses.host);
        tes3x_log_hex3("net.dns", ses.dns, ses.dns_queries, ses.dns_answers);
    }
    if (ses.server || ses.host[0]) {
        tes3x_log_hex3("net.session", ses.state, ses.id, ses.client);
        tes3x_log_hex3("net.joins", ses.hellos, ses.welcomes, ses.timeouts);
        tes3x_log_hex3("net.beats", ses.beats_out, ses.beats_in, ses.gaps);
        tes3x_log_hex3("net.rtt_us", ses.rtt_min, ses.rtt_count ? ses.rtt_sum / ses.rtt_count : 0,
                       ses.rtt_max);
        tes3x_log_hex3("net.states", ses.states_out, ses.peers_in, 0);
        tes3x_log_hex3("net.load_order", ses.plugins, ses.plugins_hash, 0);
        tes3x_log_hex3("net.events_out", rel.queued, rel.sent, rel.resent);
        tes3x_log_hex3("net.events_ack", rel.acked, rel.full, rel.dropped);
        tes3x_log_hex3("net.events_in", rel.delivered, rel.handled, rel.stale);
        for (i = 0; i < PEERS; i++)
            if (peers[i].client)
                tes3x_log_hex3("net.peer_state", peers[i].client, peers[i].seq,
                               now_us() - peers[i].time);
        tes3x_log_hex3("net.ghosts", ghost_places, ghost_moves, ghost_failures);
        ghost_heading_stat();
        tes3x_log_hex3("net.equipment_stat", equip_sent, equip_received, equip_applied);
        tes3x_log_hex3("net.identity_stat", identity_sent, identity_received, identity_applied);
        tes3x_log("net.identity_bad", identity_refused);
        tes3x_log_hex3("net.actor_equip", actor_equip_sent, actor_equip_received,
                       actor_equip_applied);
        tes3x_log("net.actor_equip_bad", actor_equip_refused);
        tes3x_log_hex3("net.stances", stance_changes, stance_refused, first_person_states);
    }
}
