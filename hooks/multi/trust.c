/* Randomness: the kernel exports none. RDTSC is sampled at every NIC interrupt and DPC, every
 * tick and frame and around trust-file reads; the game thread hashes the samples into a pool
 * with BLAKE2b and draws from it with keyed BLAKE2b once ENTROPY_NEEDED samples are in. */
#define ENTROPY_RING 64u
#define ENTROPY_NEEDED 512u

static volatile u32 entropy_ring[ENTROPY_RING], entropy_in;
static struct {
    u32 out, mixed, drawn;
    u8 pool[64];
} entropy;

/* Any context; a sample lost to a race costs nothing. */
static void entropy_add(void)
{
    u32 lo, hi;

    __asm__ volatile("rdtsc" : "=a"(lo), "=d"(hi));
    entropy_ring[entropy_in++ % ENTROPY_RING] = lo;
}

static void entropy_mix(void)
{
    crypto_blake2b_ctx ctx;
    u32 in = entropy_in, n = in - entropy.out, i;
    u8 sample[4];

    if (!n)
        return;
    if (n > ENTROPY_RING)
        n = ENTROPY_RING;
    crypto_blake2b_init(&ctx, sizeof(entropy.pool));
    crypto_blake2b_update(&ctx, entropy.pool, sizeof(entropy.pool));
    for (i = in - n; i != in; i++) {
        put32le(sample, entropy_ring[i % ENTROPY_RING]);
        crypto_blake2b_update(&ctx, sample, sizeof(sample));
    }
    crypto_blake2b_final(&ctx, entropy.pool);
    entropy.mixed += n;
    entropy.out = in;
}

/* Game thread. Up to 64 bytes; 0 until the pool has ENTROPY_NEEDED samples. */
static int random_bytes(u8 *out, u32 n)
{
    u8 block[64], label[8] = {0, 0, 0, 0, 'o', 'u', 't', 0};

    entropy_add();
    entropy_mix();
    if (entropy.mixed < ENTROPY_NEEDED || n > sizeof(block))
        return 0;
    put32le(label, ++entropy.drawn);
    crypto_blake2b_keyed(block, sizeof(block), entropy.pool, sizeof(entropy.pool), label, 8);
    copy(out, block, n);
    label[4] = 'n';
    label[5] = 'e';
    label[6] = 'x';
    label[7] = 't';
    crypto_blake2b_keyed(block, sizeof(block), entropy.pool, sizeof(entropy.pool), label, 8);
    copy(entropy.pool, block, sizeof(block));
    crypto_wipe(block, sizeof(block));
    return 1;
}

/* U:\TES3X\servers.ini keeps, per server as NetServer names it, the server's pinned static key,
 * this console's own secret key for it (its identity there) and the password typed at Join, which
 * goes in place of NetPassword. Unknown lines are kept. */
#define TRUST_TEXT 4096u

static char trust_path[] = "U:\\TES3X\\servers.ini";
static char trust_new[] = "U:\\TES3X\\servers.ini.new";
static struct {
    u32 loaded, text_n, has_fingerprint, has_server, has_client, dirty, password_n;
    char name[HOST_NAME + 8], password[PASSWORD_MAX + 1];
    u8 fingerprint[TRUST_FINGERPRINT], server[NOISE_KEY], client[NOISE_KEY];
    char text[TRUST_TEXT];
} trust;
static struct {
    volatile u32 pending;
    u32 len;
    char text[TRUST_TEXT + 256];
} trust_job;

static int hex_digit(char c)
{
    return c >= '0' && c <= '9' ? c - '0' : c >= 'a' && c <= 'f' ? c - 'a' + 10 :
           c >= 'A' && c <= 'F' ? c - 'A' + 10 : -1;
}

/* 1 if text starts with 2n hex digits, read into out. */
static int hex_read(const char *text, u8 *out, u32 n)
{
    u32 i;
    int hi, lo;

    for (i = 0; i < n; i++) {
        if ((hi = hex_digit(text[2 * i])) < 0 || (lo = hex_digit(text[2 * i + 1])) < 0)
            return 0;
        out[i] = (u8)(hi << 4 | lo);
    }
    return 1;
}

static u32 hex_write(char *out, const u8 *p, u32 n)
{
    static const char digits[] = "0123456789abcdef";
    u32 i;

    for (i = 0; i < n; i++) {
        out[2 * i] = digits[p[i] >> 4];
        out[2 * i + 1] = digits[p[i] & 15];
    }
    return 2 * n;
}

/* The length of the line at text, without its end. */
static u32 line_length(const char *text, u32 left)
{
    u32 n = 0;

    while (n < left && text[n] != '\r' && text[n] != '\n')
        n++;
    return n;
}

/* 1 if the line is "[name]" for this server, ignoring case. */
static int trust_section(const char *line, u32 n)
{
    u32 i, len = tes3x_strlen(trust.name);

    if (n != len + 2 || line[0] != '[' || line[n - 1] != ']')
        return 0;
    for (i = 0; i < len; i++)
        if ((line[1 + i] | 0x20) != (trust.name[i] | 0x20))
            return 0;
    return 1;
}

static int starts(const char *line, u32 n, const char *key)
{
    u32 i;

    for (i = 0; key[i]; i++)
        if (i >= n || line[i] != key[i])
            return 0;
    return 1;
}

/* Game thread, at `up`: the section is NetServer as given, without its fingerprint. */
static void trust_configure(const char *name, u32 n, const u8 *fingerprint)
{
    trust.loaded = 0;
    trust.has_fingerprint = fingerprint != 0;
    if (fingerprint)
        copy(trust.fingerprint, fingerprint, TRUST_FINGERPRINT);
    if (n >= sizeof(trust.name))
        n = sizeof(trust.name) - 1;
    copy((u8 *)trust.name, (const u8 *)name, n);
    trust.name[n] = 0;
}

/* Game thread, once: "build" from the head of D:\tes3xbuild.json, where the pipeline puts it
 * first. Zero without a manifest, which a server judges by the load order alone. */
static void build_id_load(void)
{
    static char path[] = "D:\\tes3xbuild.json";
    static const char key[] = "\"build\": \"";
    static char head[1024];
    static u32 loaded;
    IO_STATUS_BLOCK iosb;
    u64 offset = 0;
    u32 i, k, n = 0;
    void *h;

    if (loaded)
        return;
    loaded = 1;
    if (bulk_open(path, GENERIC_READ, FILE_OPEN, 0, &h))
        return;
    if (!NtReadFile(h, 0, 0, 0, &iosb, head, sizeof(head), &offset))
        n = iosb.Information;
    NtClose(h);
    for (i = 0; i + sizeof(key) - 1 + 2 * BUILD_ID <= n; i++) {
        for (k = 0; key[k] && head[i + k] == key[k]; k++)
            ;
        if (key[k])
            continue;
        if (!hex_read(head + i + k, build_id, BUILD_ID))
            for (k = 0; k < BUILD_ID; k++)
                build_id[k] = 0;
        break;
    }
    tes3x_log_hex3("net.build_id", get32(build_id), get32(build_id + 4), n);
}

/* Game thread, before the first handshake of each `up`. */
static void trust_load(void)
{
    IO_STATUS_BLOCK iosb;
    u64 offset = 0, size;
    u32 off, n, inside = 0;
    void *h;

    if (trust.loaded)
        return;
    trust.loaded = 1;
    trust.text_n = 0;
    trust.has_server = trust.has_client = trust.dirty = trust.password_n = 0;
    entropy_add();
    if (bulk_open(trust_path, GENERIC_READ, FILE_OPEN, 0, &h))
        return;
    size = bulk_size(h);
    if (size >= TRUST_TEXT) {
        trust.text_n = TRUST_TEXT; /* too big to rewrite safely: keys stay in memory only */
        tes3x_log("net.trust_too_big", (u32)size);
    } else if (size && !NtReadFile(h, 0, 0, 0, &iosb, trust.text, (u32)size, &offset)) {
        trust.text_n = iosb.Information;
    }
    NtClose(h);
    entropy_add();
    for (off = 0; off < trust.text_n && trust.text_n < TRUST_TEXT; off += n + 1) {
        const char *line = trust.text + off;
        n = line_length(line, trust.text_n - off);
        if (n && line[0] == '[')
            inside = trust_section(line, n);
        else if (inside && starts(line, n, "server_key=") && n >= 11 + 2 * NOISE_KEY)
            trust.has_server = hex_read(line + 11, trust.server, NOISE_KEY);
        else if (inside && starts(line, n, "client_key=") && n >= 11 + 2 * NOISE_KEY)
            trust.has_client = hex_read(line + 11, trust.client, NOISE_KEY);
        else if (inside && starts(line, n, "password=") && n - 9 <= PASSWORD_MAX)
            copy((u8 *)trust.password, (const u8 *)line + 9, trust.password_n = n - 9);
    }
    tes3x_log_hex3("net.trust", trust.text_n, trust.has_server, trust.has_client);
}

/* Game thread: the file with this server's section last, for trust_write. */
static void trust_save(void)
{
    static char out[TRUST_TEXT + 256];
    u32 off, n, len = 0, inside = 0;

    if (trust.text_n >= TRUST_TEXT)
        return;
    for (off = 0; off < trust.text_n; off += n + 1) {
        const char *line = trust.text + off;
        n = line_length(line, trust.text_n - off);
        if (n && line[0] == '[')
            inside = trust_section(line, n);
        if (!inside && n && len + n + 2 <= TRUST_TEXT) {
            copy((u8 *)out + len, (const u8 *)line, n);
            len += n;
            out[len++] = '\r';
            out[len++] = '\n';
        }
    }
    out[len++] = '[';
    copy((u8 *)out + len, (const u8 *)trust.name, tes3x_strlen(trust.name));
    len += tes3x_strlen(trust.name);
    copy((u8 *)out + len, (const u8 *)"]\r\nserver_key=", 14);
    len += 14;
    len += hex_write(out + len, trust.server, NOISE_KEY);
    copy((u8 *)out + len, (const u8 *)"\r\nclient_key=", 13);
    len += 13;
    len += hex_write(out + len, trust.client, NOISE_KEY);
    if (trust.password_n) {
        copy((u8 *)out + len, (const u8 *)"\r\npassword=", 11);
        len += 11;
        copy((u8 *)out + len, (const u8 *)trust.password, trust.password_n);
        len += trust.password_n;
    }
    out[len++] = '\r';
    out[len++] = '\n';
    if (len <= TRUST_TEXT) {
        copy((u8 *)trust.text, (const u8 *)out, len);
        trust.text_n = len;
    }
    copy((u8 *)trust_job.text, (const u8 *)out, len);
    trust_job.len = len;
    trust_job.pending = 1;
    trust.dirty = 0;
    crypto_wipe(out, sizeof(out));
}

/* File work: through a new file and a rename, so a failed write never loses the keys there. */
static void trust_write(void)
{
    IO_STATUS_BLOCK iosb;
    u32 status;
    void *h;

    if (!trust_job.pending)
        return;
    status = bulk_open("U:\\TES3X", GENERIC_READ, FILE_OPEN_IF, FILE_DIRECTORY_FILE, &h);
    if (!status) {
        NtClose(h);
        status = bulk_open(trust_new, GENERIC_WRITE, FILE_OVERWRITE_IF, 0, &h);
    }
    if (!status) {
        u64 offset = 0;
        status = NtWriteFile(h, 0, 0, 0, &iosb, trust_job.text, trust_job.len, &offset);
        NtFlushBuffersFile(h, &iosb);
        NtClose(h);
    }
    if (!status)
        status = file_replace(trust_new, trust_path);
    worker_log("net.trust_saved", trust_job.len, status, 0);
    crypto_wipe(trust_job.text, sizeof(trust_job.text));
    trust_job.pending = 0;
}
