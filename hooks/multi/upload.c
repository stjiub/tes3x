/* Bulk transfer to the server, the same packets the other way: `tes3xnet send NAME` offers
 * U:\TES3X\NAME as an OFFER event, and the server's BULK_ACK says which chunk it needs next,
 * which of those after it arrived and how many may be in flight. The file work hashes the file
 * and keeps the window's chunks in UP_SLOTS; the receive DPC, the tick and the file work send
 * them. A chunk goes again after UP_RESEND_US, or at once when one sent after it has arrived.
 * After a WELCOME the offer goes again, and the server resumes from its part. */
#define UP_SLOTS 8u
#define UP_EMPTY 0xFFFFFFFFu
#define UP_RESEND_US 500000u
#define UP_PROBE_US 1000000u
#define UP_IDLE 0u
#define UP_WANT 1u    /* named; the file work opens and hashes it */
#define UP_OFFER 2u   /* hashed; the game thread queues the OFFER */
#define UP_OFFERED 3u /* waiting for the server's first ack */
#define UP_SENDING 4u
#define UP_DONE 5u
#define UP_FAILED 6u /* unreadable here, or refused or failed there */
static struct {
    u32 state, id, total, chunks, next, window, seen, status, welcomes, last_ack, first;
    u32 index[UP_SLOTS], serial[UP_SLOTS], sent_at[UP_SLOTS], lost, serials;
    u32 sent, resent, fast, acks, logged;
    void *file;
    u8 hash[BULK_HASH];
    char name[BULK_NAME + 1];
    char path[80]; /* read from here when set, else U:\TES3X\NAME */
    u8 slot[UP_SLOTS][BULK_CHUNK];
} up;

/* Caller holds the lock. */
static void up_send_slot(u32 s, u32 now)
{
    static u8 body[8 + BULK_CHUNK];
    u32 i = up.index[s], n = i + 1 < up.chunks ? BULK_CHUNK : up.total - i * BULK_CHUNK;

    put32le(body, up.id);
    put32le(body + 4, i);
    copy(body + 8, up.slot[s], n);
    session_send(T3MP_CHUNK, body, 8 + n);
    up.resent += up.serial[s] != 0;
    up.fast += up.lost >> s & 1;
    up.lost &= ~(1u << s);
    up.serial[s] = ++up.serials;
    up.sent_at[s] = now;
    up.sent++;
}

/* Caller holds the lock: send what is loaded and due; with nothing due and no ack for a while, one
 * chunk again, to draw an ack whose predecessor was lost. */
static void up_pump(void)
{
    u32 i, s, now = now_us(), limit, sent = 0;

    if (up.state != UP_SENDING || ses.state != SESSION_JOINED)
        return;
    limit = up.window < UP_SLOTS ? up.window : UP_SLOTS;
    for (i = up.next; i < up.next + limit && i < up.chunks; i++) {
        s = i % UP_SLOTS;
        if (up.index[s] != i || (up.seen >> (i - up.next) & 1))
            continue;
        if (up.serial[s] && !(up.lost >> s & 1) && now - up.sent_at[s] < UP_RESEND_US)
            continue;
        up_send_slot(s, now);
        sent++;
    }
    s = up.next % UP_SLOTS;
    if (!sent && now - up.last_ack >= UP_PROBE_US && up.next < up.chunks && up.index[s] == up.next) {
        up.last_ack = now;
        up_send_slot(s, now);
    }
}

/* Caller holds the lock (the receive DPC). */
static void up_ack_rx(const u8 *p, u32 n)
{
    u32 next, seen, k, s, top, last;

    if (n < BULK_ACK_BYTES || !up.id || get32le(p) != up.id ||
        (up.state != UP_OFFERED && up.state != UP_SENDING) || (next = get32le(p + 4)) > up.chunks)
        return;
    up.acks++;
    up.last_ack = now_us();
    if (up.state == UP_OFFERED)
        up.first = next;
    seen = get32le(p + 8) & ((1u << UP_SLOTS) - 1);
    up.next = next;
    up.seen = seen;
    up.window = get32le(p + 12);
    up.status = get32le(p + 16);
    if (up.status != BULK_RECEIVING) {
        up.state = up.status == BULK_DONE ? UP_DONE : UP_FAILED;
        return;
    }
    up.state = UP_SENDING;
    for (top = 0, k = 1; k < UP_SLOTS; k++)
        if (seen >> k & 1)
            top = k;
    if (top && up.index[s = (next + top) % UP_SLOTS] == next + top) {
        last = up.serial[s];
        for (k = 0; k < top; k++)
            if (!(seen >> k & 1) && up.index[s = (next + k) % UP_SLOTS] == next + k &&
                up.serial[s] && up.serial[s] < last)
                up.lost |= 1u << s;
    }
    up_pump();
}

static void up_close(void)
{
    if (up.file)
        NtClose(up.file);
    up.file = 0;
}

/* File work: open and hash a named file, then keep the window's chunks loaded. */
static void up_work(void)
{
    crypto_blake2b_ctx ctx;
    IO_STATUS_BLOCK iosb;
    char path[sizeof(up.path)];
    u64 offset, size;
    u32 flags, i, s, n, left, limit, state = UP_FAILED;

    if (up.state == UP_WANT) {
        up_close();
        if (up.path[0])
            copy((u8 *)path, (const u8 *)up.path, sizeof(path));
        else
            named_path(path, up.name, "");
        if (!bulk_open(path, GENERIC_READ, FILE_OPEN, 0, &up.file) &&
            (size = bulk_size(up.file)) <= BULK_MAX) {
            crypto_blake2b_init(&ctx, BULK_HASH);
            for (offset = 0, left = (u32)size; left; offset += n, left -= n) {
                n = left < sizeof(up.slot) ? left : sizeof(up.slot);
                if (NtReadFile(up.file, 0, 0, 0, &iosb, up.slot, n, &offset) ||
                    iosb.Information != n)
                    break;
                crypto_blake2b_update(&ctx, (const u8 *)up.slot, n);
            }
            crypto_blake2b_final(&ctx, up.hash);
            if (!left)
                state = UP_OFFER;
            up.total = (u32)size;
        }
        flags = lock();
        up.id = get32le(up.hash) ? get32le(up.hash) : 1;
        up.chunks = (up.total + BULK_CHUNK - 1) / BULK_CHUNK;
        up.next = up.window = up.seen = up.lost = 0;
        for (i = 0; i < UP_SLOTS; i++)
            up.index[i] = UP_EMPTY;
        up.state = state;
        unlock(flags);
        worker_log("net.upload_ready", up.id, up.total, state);
    }
    if (up.state == UP_SENDING) {
        limit = up.window < UP_SLOTS ? up.window : UP_SLOTS;
        for (i = up.next; i < up.next + limit && i < up.chunks; i++) {
            if (up.index[s = i % UP_SLOTS] == i)
                continue;
            /* The slot's chunk is acked, so neither the DPC nor the tick sends it now. */
            n = i + 1 < up.chunks ? BULK_CHUNK : up.total - i * BULK_CHUNK;
            offset = (u64)i * BULK_CHUNK;
            if (NtReadFile(up.file, 0, 0, 0, &iosb, up.slot[s], n, &offset) ||
                iosb.Information != n) {
                flags = lock();
                up.state = UP_FAILED;
                unlock(flags);
                worker_log("net.upload_read_failed", up.id, i, 0);
                break;
            }
            flags = lock();
            up.index[s] = i;
            up.serial[s] = 0;
            up.lost &= ~(1u << s);
            up_pump();
            unlock(flags);
        }
    }
    if ((up.state == UP_DONE || up.state == UP_FAILED) && up.file)
        up_close();
}

/* Game thread: offer what is hashed, and again after a WELCOME. */
static void up_frame(void)
{
    u8 data[BULK_OFFER_BYTES + BULK_NAME + 1];
    u32 flags, n;

    flags = lock();
    if ((up.state == UP_OFFERED || up.state == UP_SENDING) && up.welcomes != ses.welcomes)
        up.state = UP_OFFER;
    unlock(flags);
    if (up.state == UP_OFFER && ses.state == SESSION_JOINED) {
        put32le(data, up.id);
        put32le(data + 4, up.total);
        copy(data + 8, up.hash, BULK_HASH);
        n = tes3x_strlen(up.name) + 1;
        copy(data + BULK_OFFER_BYTES, (const u8 *)up.name, n);
        if (event_queue(EVENT_OFFER, data, BULK_OFFER_BYTES + n)) {
            flags = lock();
            up.welcomes = ses.welcomes;
            up.last_ack = now_us();
            up.state = UP_OFFERED;
            unlock(flags);
        }
    }
    if (up.state != up.logged) {
        up.logged = up.state;
        tes3x_log_hex3("net.upload_state", up.id, up.state, up.next);
    }
}

/* Game thread: `tes3xnet send NAME`. */
/* Sends NAME, read from path when given; 0 if NAME is not a plain file name or an upload is under
 * way. */
static int up_start(const char *name, const char *path)
{
    u32 i, flags;

    for (i = 0; i < BULK_NAME && name[i]; i++) {
        char c = name[i];
        if (!((c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9') ||
              c == ' ' || c == '.' || c == '-' || c == '_') || (!i && c == '.'))
            break;
    }
    if (!i || name[i] || (up.state >= UP_WANT && up.state <= UP_SENDING) ||
        (path && tes3x_strlen(path) >= sizeof(up.path))) {
        tes3x_log("net.upload_refused", up.state);
        return 0;
    }
    copy((u8 *)up.name, (const u8 *)name, i + 1);
    up.path[0] = 0;
    if (path)
        copy((u8 *)up.path, (const u8 *)path, tes3x_strlen(path) + 1);
    flags = lock();
    up.state = UP_WANT;
    up.sent = up.resent = up.fast = up.acks = 0;
    unlock(flags);
    log_text("net.upload_name", up.name);
    return 1;
}

static void up_command(const char *name)
{
    up_start(name, 0);
}

static void up_stat(void)
{
    if (!up.id)
        return;
    tes3x_log_hex3("net.upload", up.id, up.state, up.next);
    tes3x_log_hex3("net.upload_chunks", up.sent, up.resent, up.fast);
    tes3x_log_hex3("net.upload_acks", up.acks, up.first, up.total);
}

static void bulk_stat(void)
{
    if (!bulk.id)
        return;
    tes3x_log_hex3("net.bulk", bulk.id, bulk.state, bulk.next);
    tes3x_log_hex3("net.bulk_chunks", bulk.arrived, bulk.duplicates, bulk.written);
    tes3x_log_hex3("net.bulk_acks", bulk.acks, bulk.resumed, bulk.total);
}
