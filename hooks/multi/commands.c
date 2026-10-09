static const char *word(const char *text, const char *w)
{
    for (; *w; text++, w++) {
        char c = *text;
        if (c >= 'A' && c <= 'Z')
            c += 'a' - 'A';
        if (c != *w)
            return 0;
    }
    return *text == ' ' || !*text ? text : 0;
}

static const char *skip(const char *text)
{
    while (*text == ' ')
        text++;
    return text;
}

static const char *number(const char *text, u32 *value)
{
    *value = 0;
    if (*text < '0' || *text > '9')
        return 0;
    while (*text >= '0' && *text <= '9')
        *value = *value * 10 + (u32)(*text++ - '0');
    return text;
}

/* A.B.C.D into *ip, host order; the text after it, or 0 if malformed. */
static const char *address(const char *text, u32 *ip)
{
    u32 part, i;

    *ip = 0;
    for (i = 0; i < 4; i++) {
        if (!(text = number(text, &part)) || part > 255)
            return 0;
        *ip = *ip << 8 | part;
        if (i < 3 && *text++ != '.')
            return 0;
    }
    return *ip ? text : 0;
}

/* A host name into out (HOST_NAME bytes): letters, digits, hyphens and dots between labels. */
static const char *host_name(const char *text, char *out)
{
    u32 n = 0, label = 0;

    for (;; text++) {
        char c = *text;
        if (c == '.' && label && label <= 63) {
            label = 0;
        } else if ((c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9') ||
                   c == '-') {
            label++;
        } else {
            break;
        }
        if (n >= HOST_NAME - 1)
            return 0;
        out[n++] = c;
    }
    out[n] = 0;
    return n && label && label <= 63 ? text : 0;
}

/* up A.B.C.D[/BITS]|dhcp [SERVER[:PORT] [GATEWAY [DNS]]], where SERVER is an address or a name;
 * with dhcp a GATEWAY or DNS given here overrides the lease's. With the NIC already up only the
 * session is opened again, to the server given. */
static void command_up(const char *text)
{
    u32 ip = 0, bits = 24, server = 0, port = PORT, gateway = 0, dns = 0, flags, named = 0;
    u32 pinned = 0, again = net.up;
    char host[HOST_NAME];
    const char *after, *name = 0;
    u8 fingerprint[TRUST_FINGERPRINT];
    int lease = 0;

    host[0] = 0;
    if ((after = word(skip(text), "dhcp"))) {
        lease = 1;
        bits = 0;
        text = after;
    } else if (!(text = address(skip(text), &ip))) {
        goto usage;
    }
    if (!lease && *text == '/' &&
        (!(text = number(text + 1, &bits)) || bits < 1 || bits > 30))
        goto usage;
    text = skip(text);
    if (*text) {
        name = text;
        if ((after = address(text, &server)) &&
            (*after == ':' || *after == '#' || *after == ' ' || !*after))
            text = after;
        else if (!(text = host_name(text, host)))
            goto usage;
        if (*text == ':' && (!(text = number(text + 1, &port)) || !port || port > 65535))
            goto usage;
        named = (u32)(text - name);
        if (*text == '#') { /* the server key's fingerprint, to pin before first contact */
            if (!hex_read(text + 1, fingerprint, TRUST_FINGERPRINT))
                goto usage;
            pinned = 1;
            text += 1 + 2 * TRUST_FINGERPRINT;
        }
        text = skip(text);
        if (*text == '-') /* no gateway, a DNS server follows */
            text++;
        else if (*text && !(text = address(text, &gateway)))
            goto usage;
        text = skip(text);
        if (*text && !(text = address(text, &dns)))
            goto usage;
        if (*skip(text))
            goto usage;
        if (host[0])
            server = 0;
        if (host[0] && !lease && !dns && !(dns = gateway)) {
            tes3x_log("net.no_dns", 0);
            goto usage;
        }
    }
    if (!again) {
        if (!tes3x_net_start(ip, 1))
            return;
        tes3x_net_set_mask(lease ? 0 : 0xFFFFFFFFu << (32 - bits));
        if (!lease)
            tes3x_net_announce();
    }
    tes3x_log_hex3(again ? "net.reopen" : "net.up", ip, bits, server);
    if (server || host[0]) {
        u32 *w = (u32 *)&ses;
        u32 n;

        flags = lock();
        if (again && ses.state == SESSION_JOINED)
            session_send(T3MP_BYE, 0, 0);
        if (again && lease) { /* what the lease gave */
            gateway = gateway ? gateway : ses.gateway;
            dns = dns ? dns : ses.dns;
        }
        for (n = 0; n < sizeof(ses) / 4; n++)
            w[n] = 0;
        multi_channel.route.hop = 0;
        multi_channel.route.known = 0;
        ses.server = server;
        ses.port = port;
        ses.gateway = gateway;
        tes3x_net_set_gateway(gateway);
        ses.dns = dns;
        copy((u8 *)ses.host, (const u8 *)host, HOST_NAME);
        handshake_reset();
        sec.keyed = 0;
        unlock(flags);
        trust_configure(name, named, pinned ? fingerprint : 0);
        load_order();
        flags = lock();
        if (lease && !(again && dhcp.state == DHCP_BOUND))
            ses.state = SESSION_DHCP;
        else
            route_to(server ? server : dns);
        unlock(flags);
        if (!lease && !again && (multi_channel.route.hop ^ ip) & net.mask)
            tes3x_log("net.no_gateway", multi_channel.route.hop);
        if (host[0])
            log_text("net.resolving", host);
        tes3x_log_hex3("net.session_start", server ? server : dns, port,
                       multi_channel.route.hop);
    }
    if (again)
        return;
    flags = lock();
    dhcp.state = DHCP_IDLE;
    if (lease) {
        u32 *w = (u32 *)&dhcp, n;

        for (n = 0; n < sizeof(dhcp) / 4; n++)
            w[n] = 0;
        dhcp.keep_gateway = gateway != 0;
        dhcp.keep_dns = dns != 0;
        ses.gateway = gateway;
        tes3x_net_set_gateway(gateway);
        ses.dns = dns;
        dhcp_discover();
    }
    unlock(flags);
    return;
usage:
    tes3x_log("net.usage", 0);
}

/* ARP for an address three times and log the replies with the receive and transmit counters,
 * so a restart can be checked against any host that answers ARP, such as the router. */
static void probe(const char *text)
{
    u32 target;

    if (!net.up || !(text = address(text, &target)) || *skip(text)) {
        tes3x_log("net.usage", 0);
        return;
    }
    tes3x_net_probe(target);
}

static u32 ini_text(const char *key, char *out, u32 size)
{
    u32 i;

    tes3x_ini_xbox(key, "", out, size);
    for (i = 0; out[i]; i++)
        ;
    while (i && out[i - 1] == ' ')
        out[--i] = 0;
    return i;
}

/* The ini reader needs the game drive, which is not mounted at process entry. A launch the main
 * menu's Join led to joins its server even without NetAddress (DHCP), and in place of NetServer. */
static void autostart(void)
{
    char line[24 + JOIN_NAME + 2 * 24];
    u32 n, server, i;

    build_id_load();
    net_password_n = ini_text("NetPassword", net_password, sizeof(net_password));
    if (!(n = ini_text("NetAddress", line, 24))) {
        if (!join_server[0])
            return;
        copy((u8 *)line, (const u8 *)"dhcp", 4);
        n = 4;
    }
    line[n++] = ' ';
    for (i = 0; join_server[i]; i++)
        line[n + i] = join_server[i];
    if (!(server = i))
        server = ini_text("NetServer", line + n, JOIN_NAME);
    if (server) {
        n += server;
        line[n++] = ' ';
        if (!(server = ini_text("NetGateway", line + n, 24)))
            line[n++] = '-';
        n += server;
        line[n++] = ' ';
        ini_text("NetDns", line + n, 24);
    }
    tes3x_log("net.autostart", 1);
    command_up(line);
}

static int mapped(const void *p)
{
    return (u32)p >= 0x10000u && (u32)p < 0x80000000u;
}

static int plausible(const void *p)
{
    return mapped(p) && !((u32)p & 3);
}

/* FNV-1a over the loaded plugins' names in load order, lowercased and each ended by a zero, as
 * tes3x.net load_order_hash. The file list is [DataHandler]: count +0xC, files +0xAE70; a
 * file's name is inline at +0xC. */
#define FILES_COUNT 0xC
#define FILES_ARRAY 0xAE70
#define FILE_NAME 0xC
#define FILE_NAME_MAX 260u

static void load_order(void)
{
    const u8 *handler = *(const u8 **)TES3X_NET_DATA_HANDLER, *list, *file;
    const char *name;
    u32 hash = 2166136261u, count, i, j;

    ses.plugins = ses.plugins_hash = 0;
    if (!plausible(handler) || !plausible(list = *(const u8 **)handler))
        return;
    count = *(const u32 *)(list + FILES_COUNT);
    if (count > 256)
        return;
    for (i = 0; i < count; i++) {
        if (!plausible(file = ((const u8 *const *)(list + FILES_ARRAY))[i]))
            return;
        name = (const char *)file + FILE_NAME;
        for (j = 0; j < FILE_NAME_MAX && name[j]; j++) {
            char c = name[j];
            if (c >= 'A' && c <= 'Z')
                c += 'a' - 'A';
            hash = (hash ^ (u8)c) * 16777619u;
        }
        hash *= 16777619u; /* the terminating zero */
        log_text("net.plugin", name);
    }
    ses.plugins = count;
    ses.plugins_hash = hash;
    tes3x_log_hex3("net.load_order", count, hash, 0);
}

/* WorldController -> MobController -> MobilePlayer -> Reference, or 0 outside the world. */
static const u8 *player_reference(void)
{
    const u8 *world = *(const u8 **)TES3X_NET_WORLD, *mobs, *mobile, *const *list, *ref;

    if (!plausible(world) || !plausible(mobs = *(const u8 **)(world + 0x5C)))
        return 0;
    list = *(const u8 *const **)(mobs + 0x24);
    if (!plausible(list) || !plausible(mobile = *list))
        return 0;
    ref = *(const u8 **)(mobile + 0x14);
    return plausible(ref) ? ref : 0;
}

static void log_text(const char *tag, const char *text)
{
    char line[64];
    u32 n = 0;

    while (*tag && n < 24)
        line[n++] = *tag++;
    line[n++] = ' ';
    while (text && *text && n < sizeof(line) - 1)
        line[n++] = *text++;
    line[n++] = '\n';
    tes3x_log_raw(line, n);
}

typedef void *(__attribute__((thiscall)) *fn_ref_part)(const void *ref);
typedef u8(__attribute__((thiscall)) *fn_ref_update)(void *ref);

/* A reference's attachments are a list of {kind, next, data...}; its mobile is kind 8. */
#define REF_ATTACHMENTS 0x44
#define ATTACHMENT_MOBILE 8u

static u8 *ref_mobile(const u8 *ref)
{
    const u8 *a = *(const u8 *const *)(ref + REF_ATTACHMENTS);
    u32 guard;

    for (guard = 0; plausible(a) && guard < 32; a = *(const u8 *const *)(a + 4), guard++)
        if (*(const u32 *)a == ATTACHMENT_MOBILE)
            return *(u8 *const *)(a + 8);
    return 0;
}
