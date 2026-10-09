/* Bulk transfer from the server. OFFER (an event) names a file, its size and its BLAKE2b-256
 * hash; CHUNK packets carry BULK_CHUNK bytes each by index; BULK_ACK tells the sender the first
 * chunk not yet written, which of the next ones arrived, how many past it may be in flight and the
 * transfer's status. The receive DPC only copies chunks into slots: the game thread writes them
 * to U:\TES3X\NAME.part in order and renames it to NAME once its hash matches. A relaunch resumes
 * from the part's length, since the server offers again after WELCOME. */
#define BULK_CHUNK 1024u
#define BULK_SLOTS 16u /* chunks in flight, well under RX_RING with the rest of the traffic */
#define BULK_HASH 32u
#define BULK_NAME 38u /* 37 characters and ".part" fit FATX's 42 */
#define BULK_MAX (16u << 20)
#define BULK_ACK_BYTES 20u
#define BULK_OFFER_BYTES (8u + BULK_HASH)
#define BULK_IDLE 0u
#define BULK_OPENING 1u
#define BULK_RECEIVING 2u
#define BULK_DONE 3u
#define BULK_BAD_HASH 4u
#define BULK_REFUSED 5u
#define BULK_FAILED 6u
#define BULK_NO_SPACE 7u /* the volume cannot take the rest of the file and BULK_SPACE_KB */
/* Left free after a download: the engine's next save into the slot writes a copy beside the old
 * one before it replaces it. */
#define BULK_SPACE_KB 1024u
static char game_loaded[BULK_NAME + 1]; /* the save this launch was made to load */
#define FILE_DIRECTORY_FILE 0x01u
#define FILE_SHARE_DELETE 0x04u
#define DELETE_ACCESS 0x00010000u
#define FileRenameInformation 10u

typedef struct {
    u8 ReplaceIfExists;
    void *RootDirectory;
    ANSI_STRING FileName;
} FILE_RENAME_INFORMATION;

#define NtCreateFile KFN(THUNK_NtCreateFile, fn_NtCreateFile)
#define NtWriteFile KFN(THUNK_NtWriteFile, fn_NtWriteFile)
#define NtReadFile KFN(THUNK_NtReadFile, fn_NtReadFile)
#define NtQueryInformationFile KFN(THUNK_NtQueryInformationFile, fn_NtQueryInformationFile)
#define NtSetInformationFile KFN(THUNK_NtSetInformationFile, fn_NtSetInformationFile)
#define NtFlushBuffersFile KFN(THUNK_NtFlushBuffersFile, fn_NtFlushBuffersFile)
#define NtClose KFN(THUNK_NtClose, fn_NtClose)
typedef u32(__stdcall *fn_NtQueryVolumeInformationFile)(void *, IO_STATUS_BLOCK *, void *, u32,
                                                        u32);
/* Xbox's NtQueryDirectoryFile has no ReturnSingleEntry; RestartScan is a BOOLEAN in a 4-byte slot. */
typedef u32(__stdcall *fn_NtQueryDirectoryFile)(void *, void *, void *, void *, IO_STATUS_BLOCK *,
                                                void *, u32, u32, ANSI_STRING *, u32);
#define NtQueryVolumeInformationFile                                                               \
    KFN(THUNK_NtQueryVolumeInformationFile, fn_NtQueryVolumeInformationFile)
#define NtQueryDirectoryFile KFN(THUNK_NtQueryDirectoryFile, fn_NtQueryDirectoryFile)
#define FileFsSizeInformation 3u
#define FileDirectoryInformation 1u

/* File work runs on a worker thread: a FATX write and flush can take tens of milliseconds (the
 * first servers.ini on hardware: 64 ms), and a bulk transfer ends by hashing the whole file. The
 * log file admits one writer at a time, so the worker queues its lines for the game thread. */
#define WORKER_SLEEP_MS 5
#define WORKER_LOGS 16u
static struct {
    volatile u32 running, stop;
    u32 count, lost;
    struct {
        const char *tag;
        u32 a, b, c;
    } log[WORKER_LOGS];
} worker;

static void worker_log(const char *tag, u32 a, u32 b, u32 c)
{
    u32 flags = lock();

    if (worker.count < WORKER_LOGS) {
        worker.log[worker.count].tag = tag;
        worker.log[worker.count].a = a;
        worker.log[worker.count].b = b;
        worker.log[worker.count++].c = c;
    } else {
        worker.lost++;
    }
    unlock(flags);
}

/* state, id, total, chunks, next and filled are shared with the receive DPC; the rest is the file
 * work's. An offer waits in bulk_offered until the file work takes it up. */
static struct {
    u32 ready, id, total, state;
    u8 hash[BULK_HASH];
    char name[BULK_NAME + 1];
} bulk_offered;
static struct {
    u32 state, id, total, chunks, next, filled; /* filled: bit s, slot s holds its chunk */
    u32 arrived, duplicates, written, resumed, acks;
    void *file;
    u8 hash[BULK_HASH];
    char name[BULK_NAME + 1];
    u8 slot[BULK_SLOTS][BULK_CHUNK];
} bulk;

static u32 bulk_chunk_bytes(u32 index)
{
    return index + 1 < bulk.chunks ? BULK_CHUNK : bulk.total - index * BULK_CHUNK;
}

/* Caller holds the lock. */
static void bulk_ack(void)
{
    u8 body[BULK_ACK_BYTES];
    u32 i, seen = 0;

    if (ses.state != SESSION_JOINED)
        return;
    for (i = 0; i < BULK_SLOTS && bulk.next + i < bulk.chunks; i++)
        if (bulk.filled >> ((bulk.next + i) % BULK_SLOTS) & 1)
            seen |= 1u << i;
    put32le(body, bulk.id);
    put32le(body + 4, bulk.next);
    put32le(body + 8, seen);
    put32le(body + 12, bulk.state == BULK_RECEIVING ? BULK_SLOTS : 0);
    put32le(body + 16, bulk.state);
    session_send(T3MP_BULK_ACK, body, sizeof(body));
    bulk.acks++;
}

/* Caller holds the lock (the receive DPC). A chunk outside the window is answered with an ack,
 * since the sender's view is behind; every fourth arrival is acked too. */
static void bulk_chunk_rx(const u8 *p, u32 n)
{
    u32 index, s;

    if (n < 8 || !bulk.id || get32le(p) != bulk.id || bulk.state == BULK_OPENING)
        return;
    index = get32le(p + 4);
    if (bulk.state != BULK_RECEIVING || index < bulk.next || index >= bulk.next + BULK_SLOTS ||
        index >= bulk.chunks || n - 8 != bulk_chunk_bytes(index) ||
        bulk.filled >> (s = index % BULK_SLOTS) & 1) {
        bulk.duplicates++;
        bulk_ack();
        return;
    }
    copy(bulk.slot[s], p + 8, n - 8);
    bulk.filled |= 1u << s;
    if (++bulk.arrived % 4 == 0)
        bulk_ack();
}

static void named_path(char *path, const char *name, const char *suffix)
{
    static const char dir[] = "U:\\TES3X\\";
    u32 n = 0, i;

    for (i = 0; dir[i]; i++)
        path[n++] = dir[i];
    for (i = 0; name[i]; i++)
        path[n++] = name[i];
    for (i = 0; suffix[i]; i++)
        path[n++] = suffix[i];
    path[n] = 0;
}

static void bulk_path(char *path, const char *suffix)
{
    named_path(path, bulk.name, suffix);
}

static u32 bulk_open(char *path, u32 access, u32 disposition, u32 options, void **h)
{
    ANSI_STRING name;
    OBJECT_ATTRIBUTES oa;
    IO_STATUS_BLOCK iosb;

    *h = 0;
    tes3x_dos_attributes(&oa, &name, path);
    return NtCreateFile(h, access | SYNCHRONIZE, &oa, &iosb, 0, FILE_ATTRIBUTE_NORMAL,
                        FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE, disposition,
                        options | FILE_SYNCHRONOUS_IO_NONALERT);
}

static u64 bulk_size(void *h)
{
    FILE_NETWORK_OPEN_INFORMATION info;
    IO_STATUS_BLOCK iosb;

    if (NtQueryInformationFile(h, &iosb, &info, sizeof(info), FileNetworkOpenInformation))
        return 0;
    return info.EndOfFile;
}

/* 1 if the file at path is total bytes long and hashes to the offer's hash. Uses the slots as
 * its buffer, so only while nothing is being received into them. */
static int bulk_verify(char *path)
{
    crypto_blake2b_ctx ctx;
    IO_STATUS_BLOCK iosb;
    u8 digest[BULK_HASH];
    u64 offset = 0;
    u32 left = bulk.total, n, i, diff = 0;
    void *h;

    if (bulk_open(path, GENERIC_READ, FILE_OPEN, 0, &h))
        return 0;
    if (bulk_size(h) != bulk.total) {
        NtClose(h);
        return 0;
    }
    crypto_blake2b_init(&ctx, BULK_HASH);
    while (left) {
        n = left < sizeof(bulk.slot) ? left : sizeof(bulk.slot);
        if (NtReadFile(h, 0, 0, 0, &iosb, bulk.slot, n, &offset) || iosb.Information != n)
            break;
        crypto_blake2b_update(&ctx, (const u8 *)bulk.slot, n);
        offset += n;
        left -= n;
    }
    NtClose(h);
    crypto_blake2b_final(&ctx, digest);
    for (i = 0; i < BULK_HASH; i++)
        diff |= digest[i] ^ bulk.hash[i];
    return !left && !diff;
}

/* Renames from to to, replacing what is there; 0 on success. */
static u32 file_replace(char *from, char *to)
{
    FILE_RENAME_INFORMATION rename;
    IO_STATUS_BLOCK iosb;
    u32 status;
    void *h;

    if ((status = bulk_open(from, DELETE_ACCESS, FILE_OPEN, 0, &h)))
        return status;
    rename.ReplaceIfExists = 1;
    rename.RootDirectory = OB_DOS_DEVICES;
    rename.FileName.Buffer = to;
    rename.FileName.Length = rename.FileName.MaximumLength = (unsigned short)tes3x_strlen(to);
    status = NtSetInformationFile(h, &iosb, &rename, sizeof(rename), FileRenameInformation);
    NtClose(h);
    return status;
}

static void bulk_close(void)
{
    if (bulk.file)
        NtClose(bulk.file);
    bulk.file = 0;
}

/* Game thread: check an offer and leave it for the file work. A plain name: letters, digits,
 * space, '.', '-', '_', not starting with a dot. */
static void bulk_offer(const struct event *e)
{
    u32 i, n = e->length - BULK_OFFER_BYTES, flags, id, total, state = BULK_OPENING;
    char name[BULK_NAME + 1];

    if (e->length < BULK_OFFER_BYTES + 1)
        return;
    id = get32le(e->data);
    total = get32le(e->data + 4);
    for (i = 0; i < n && i < BULK_NAME && e->data[BULK_OFFER_BYTES + i]; i++) {
        char c = (char)e->data[BULK_OFFER_BYTES + i];
        if (!((c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9') ||
              c == ' ' || c == '.' || c == '-' || c == '_') || (!i && c == '.'))
            break;
        name[i] = c;
    }
    name[i] = 0;
    if (!i || i == BULK_NAME || (i < n && e->data[BULK_OFFER_BYTES + i]) || !id ||
        total > BULK_MAX)
        state = BULK_REFUSED;
    tes3x_log_hex3("net.bulk_offer", id, total, state);
    log_text("net.bulk_name", name);
    flags = lock();
    bulk_offered.id = id;
    bulk_offered.total = total;
    bulk_offered.state = state;
    copy(bulk_offered.hash, e->data + 8, BULK_HASH);
    copy((u8 *)bulk_offered.name, (const u8 *)name, BULK_NAME + 1);
    bulk_offered.ready = 1;
    unlock(flags);
}

/* File work: take up the latest offer, dropping the transfer before it. */
static void bulk_adopt(void)
{
    u32 flags;

    if (!bulk_offered.ready)
        return;
    bulk_close();
    flags = lock();
    bulk.id = bulk_offered.id;
    bulk.total = bulk_offered.total;
    bulk.chunks = (bulk.total + BULK_CHUNK - 1) / BULK_CHUNK;
    copy(bulk.hash, bulk_offered.hash, BULK_HASH);
    copy((u8 *)bulk.name, (const u8 *)bulk_offered.name, BULK_NAME + 1);
    bulk.filled = bulk.next = 0;
    bulk.state = bulk_offered.state;
    bulk_offered.ready = 0;
    if (bulk.state == BULK_REFUSED)
        bulk_ack();
    unlock(flags);
}

/* File work: open or resume the part, then write what arrived, in order. */
/* KB free on the volume holding h; all of it when the volume will not say. */
static u32 volume_free_kb(void *h)
{
    struct {
        u64 total, available;
        u32 sectors, bytes;
    } fs;
    IO_STATUS_BLOCK iosb;
    u32 cluster_kb;

    if (NtQueryVolumeInformationFile(h, &iosb, &fs, sizeof(fs), FileFsSizeInformation))
        return 0xFFFFFFFFu;
    cluster_kb = fs.sectors * fs.bytes / 1024;
    if (fs.available >> 32 || (cluster_kb && (u32)fs.available > 0xFFFFFFFFu / cluster_kb))
        return 0xFFFFFFFFu;
    return (u32)fs.available * cluster_kb;
}

static int same_name(const char *a, const char *b)
{
    u32 i;

    for (i = 0; a[i] && (a[i] | 0x20) == (b[i] | 0x20); i++)
        ;
    return !a[i] && !b[i];
}

/* Once per launch, before any transfer: checkpoints sent by the server (char-*.ess and their
 * parts) other than the one this launch loaded. A new one arrives with each join that needs it. */
#define SWEEP_FILES 32u
static u32 sweep_done, swept;

static void checkpoint_sweep(void)
{
    static u8 info[1024];
    static char names[SWEEP_FILES][BULK_NAME + 6];
    char mask_text[] = "char-*", path[16 + BULK_NAME + 8];
    ANSI_STRING mask;
    IO_STATUS_BLOCK iosb;
    u32 count = 0, n, off, i, restart = 1, gone = 0;
    const u8 *e;
    void *dir, *h;
    u8 del = 1;

    if (sweep_done)
        return;
    sweep_done = 1;
    if (bulk_open("U:\\TES3X", GENERIC_READ, FILE_OPEN, FILE_DIRECTORY_FILE, &dir))
        return;
    mask.Buffer = mask_text;
    mask.Length = mask.MaximumLength = sizeof(mask_text) - 1;
    while (count < SWEEP_FILES &&
           !NtQueryDirectoryFile(dir, 0, 0, 0, &iosb, info, sizeof(info),
                                 FileDirectoryInformation, &mask, restart)) {
        restart = 0;
        for (off = 0; off < sizeof(info) && count < SWEEP_FILES; off += n) {
            e = info + off;
            i = *(const u32 *)(e + 0x3C);
            if (i && i < sizeof(names[0])) {
                copy((u8 *)names[count], e + 0x40, i);
                names[count][i] = 0;
                if (!same_name(names[count], game_loaded))
                    count++;
            }
            if (!(n = *(const u32 *)e))
                break;
        }
    }
    NtClose(dir);
    for (i = 0; i < count; i++) {
        named_path(path, names[i], "");
        if (!bulk_open(path, DELETE_ACCESS, FILE_OPEN, 0, &h)) {
            gone += !NtSetInformationFile(h, &iosb, &del, sizeof(del), FileDispositionInformation);
            NtClose(h);
        }
    }
    swept = gone;
    worker_log("net.checkpoints_swept", count, gone, 0);
}

static void bulk_work(void)
{
    char path[16 + BULK_NAME + 8], part[16 + BULK_NAME + 8];
    IO_STATUS_BLOCK iosb;
    u64 offset, size;
    u32 flags, s, n, next, state, wrote = 0, need, free;
    void *h;

    checkpoint_sweep();
    bulk_adopt();
    if (bulk.state == BULK_OPENING) {
        bulk_path(path, "");
        bulk_path(part, ".part");
        state = BULK_RECEIVING;
        next = 0;
        if (bulk_verify(path)) {
            state = BULK_DONE;
            next = bulk.chunks;
        } else if (bulk_open("U:\\TES3X", GENERIC_READ, FILE_OPEN_IF, FILE_DIRECTORY_FILE, &h)) {
            state = BULK_FAILED;
        } else {
            NtClose(h);
            if (bulk_open(part, GENERIC_READ | GENERIC_WRITE, FILE_OPEN_IF, 0, &bulk.file)) {
                state = BULK_FAILED;
            } else {
                size = bulk_size(bulk.file);
                next = size > bulk.total ? 0 : (u32)size / BULK_CHUNK;
                if (next)
                    bulk.resumed++;
                need = (bulk.total - next * BULK_CHUNK + 1023) / 1024 + BULK_SPACE_KB;
                free = volume_free_kb(bulk.file);
                worker_log("net.bulk_space", bulk.id, free, need);
                if (free < need) {
                    worker_log("net.bulk_no_space", bulk.id, free, need);
                    bulk_close();
                    state = BULK_NO_SPACE;
                }
            }
        }
        worker_log("net.bulk_start", bulk.id, next, state);
        flags = lock();
        bulk.next = next;
        bulk.state = state;
        bulk_ack();
        unlock(flags);
    }
    if (bulk.state != BULK_RECEIVING)
        return;
    for (;;) {
        s = bulk.next % BULK_SLOTS;
        if (bulk.next >= bulk.chunks || !(bulk.filled >> s & 1))
            break;
        /* The DPC writes this slot only once next has moved past it. */
        n = bulk_chunk_bytes(bulk.next);
        offset = (u64)bulk.next * BULK_CHUNK;
        if (NtWriteFile(bulk.file, 0, 0, 0, &iosb, bulk.slot[s], n, &offset) ||
            iosb.Information != n) {
            worker_log("net.bulk_write_failed", bulk.id, bulk.next, 0);
            bulk_close();
            flags = lock();
            bulk.state = BULK_FAILED;
            bulk_ack();
            unlock(flags);
            return;
        }
        flags = lock();
        bulk.filled &= ~(1u << s);
        bulk.next++;
        unlock(flags);
        bulk.written++;
        wrote = 1;
    }
    if (bulk.next < bulk.chunks) {
        if (wrote) {
            flags = lock();
            bulk_ack();
            unlock(flags);
        }
        return;
    }
    offset = bulk.total;
    NtSetInformationFile(bulk.file, &iosb, &offset, sizeof(offset), FileEndOfFileInformation);
    NtFlushBuffersFile(bulk.file, &iosb);
    bulk_close();
    bulk_path(path, "");
    bulk_path(part, ".part");
    state = BULK_BAD_HASH;
    if (bulk_verify(part)) {
        state = file_replace(part, path) ? BULK_FAILED : BULK_DONE;
    } else if (!bulk_open(part, GENERIC_WRITE, FILE_OPEN, 0, &h)) {
        offset = 0; /* start over */
        NtSetInformationFile(h, &iosb, &offset, sizeof(offset), FileEndOfFileInformation);
        NtClose(h);
    }
    worker_log("net.bulk_done", bulk.id, bulk.total, state);
    flags = lock();
    bulk.state = state;
    bulk_ack();
    unlock(flags);
}

/* Every TICK_MS; caller holds the lock. The sender resends what it has not seen acked. */
static void bulk_tick(void)
{
    if (bulk.state == BULK_RECEIVING)
        bulk_ack();
}
