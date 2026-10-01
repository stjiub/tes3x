/* Read what a build folder lacks from another game folder, [Xbox] OverlayBase.
 * Every file call reaches the kernel through the import thunks, so the shims replace those slots.
 * Only read-only opens of D:\ paths fall back; writes and every other drive pass through.
 * D: is not mounted at entry, so the ini is read on the first D:\ access instead. */

#include "tes3x_thunks.h"
#include "tes3xnt.h"
#include "tes3xlog.h"

typedef u32(__stdcall *fn_NtOpenFile)(void **, u32, OBJECT_ATTRIBUTES *, IO_STATUS_BLOCK *, u32,
                                      u32);
typedef u32(__stdcall *fn_NtQueryFullAttributesFile)(OBJECT_ATTRIBUTES *,
                                                     FILE_NETWORK_OPEN_INFORMATION *);
/* Xbox's NtQueryDirectoryFile has no ReturnSingleEntry; RestartScan is a BOOLEAN in a 4-byte slot. */
typedef u32(__stdcall *fn_NtQueryDirectoryFile)(void *, void *, void *, void *, IO_STATUS_BLOCK *,
                                                void *, u32, u32, ANSI_STRING *, u32);

#define STATUS_NO_MORE_FILES 0x80000006u
#define STATUS_NO_SUCH_FILE 0xC000000Fu
#define STATUS_OBJECT_NAME_NOT_FOUND 0xC0000034u
#define STATUS_OBJECT_PATH_NOT_FOUND 0xC000003Au
#define FILE_DIRECTORY_FILE 0x01u
#define FILE_DELETE_ON_CLOSE 0x1000u
#define FileDirectoryInformation 1u
#define WRITE_ACCESS 0x500D0116u /* GENERIC_WRITE|GENERIC_ALL|WRITE_OWNER|WRITE_DAC|DELETE|
                                    FILE_WRITE_ATTRIBUTES|FILE_WRITE_EA|FILE_APPEND_DATA|
                                    FILE_WRITE_DATA */

#define PATH_MAX 512
#define PAIRS 8
#define PAIR_CLAIMED ((void *)1)
#define LOGGED_PATHS 8
#define LOGGED_OWN 16
#define ARCHIVES 8

volatile u32 tes3x_overlay_installed;

static fn_NtCreateFile orig_create;
static fn_NtOpenFile orig_open;
static fn_NtQueryFullAttributesFile orig_attrs;
static fn_NtQueryDirectoryFile orig_query_dir;
static fn_NtClose orig_close;

static char base[260];
static u32 base_len;
static volatile long state; /* 0 not configured, 1 configuring, 2 on, 3 off */
static fn_NtReadFile orig_read;
static u32 hits_open, hits_attrs, hits_dirs, hits_dups, hits_own, logged, logged_own;

/* Open archives, so reads show which one the engine takes assets from. */
static void *volatile archives[ARCHIVES];
static u32 archive_reads[ARCHIVES];
static const char *const archive_tags[ARCHIVES] = {
    "overlay.bsa0.reads", "overlay.bsa1.reads", "overlay.bsa2.reads", "overlay.bsa3.reads",
    "overlay.bsa4.reads", "overlay.bsa5.reads", "overlay.bsa6.reads", "overlay.bsa7.reads",
};

/* A D:\ directory open in both folders: list the build, then the base minus what the build has. */
typedef struct {
    void *volatile build;
    void *base;
    u32 phase; /* 0 build, 1 base not started, 2 base started */
    int has_mask;
    ANSI_STRING mask;
    char mask_buf[64];
    u32 dir_len;
    char dir[260];
} PAIR;

static PAIR pairs[PAIRS];

static void count(const char *tag, u32 *n)
{
    u32 v = ++*n;
    if ((v & (v - 1)) == 0)
        tes3x_log(tag, v);
}

static void log_path(const char *tag, const char *s, u32 n)
{
    if (logged >= LOGGED_PATHS)
        return;
    logged++;
    tes3x_log_raw(tag, tes3x_strlen(tag));
    tes3x_log_raw(s, n);
    tes3x_log_raw("\n", 1);
}

/* Offset of the "\..." remainder of a D:\ name, or 0. */
static u32 d_rest(OBJECT_ATTRIBUTES *oa)
{
    const char *s;
    u32 n;

    if (!oa || !oa->ObjectName || !oa->ObjectName->Buffer)
        return 0;
    s = oa->ObjectName->Buffer;
    n = oa->ObjectName->Length;
    if (oa->RootDirectory == OB_DOS_DEVICES && n >= 3 && (s[0] | 0x20) == 'd' && s[1] == ':'
            && s[2] == '\\')
        return 2;
    if (!oa->RootDirectory && n >= 7 && s[0] == '\\' && s[1] == '?' && s[2] == '?'
            && s[3] == '\\' && (s[4] | 0x20) == 'd' && s[5] == ':' && s[6] == '\\')
        return 6;
    return 0;
}

static void configure(void);
static int same(const char *a, const char *b);

static int overlay_on(void)
{
    if (state == 0 && __sync_bool_compare_and_swap(&state, 0, 1))
        configure();
    return state == 2;
}

static void note_archive(void *h, const char *s, u32 n)
{
    static char tag[] = "overlay.bsa0 ";
    int i;

    if (n < 4 || s[n - 4] != '.' || (s[n - 3] | 0x20) != 'b' || (s[n - 2] | 0x20) != 's'
            || (s[n - 1] | 0x20) != 'a')
        return;
    for (i = 0; i < ARCHIVES; i++)
        if (__sync_bool_compare_and_swap(&archives[i], (void *)0, h)) {
            tag[11] = (char)('0' + i);
            tes3x_log_raw(tag, sizeof(tag) - 1);
            tes3x_log_raw(s, n);
            tes3x_log_raw("\n", 1);
            return;
        }
}

/* A D:\ file the build folder served itself. */
static void note_own(void *h, OBJECT_ATTRIBUTES *oa, u32 options)
{
    if ((options & FILE_DIRECTORY_FILE) || !d_rest(oa) || !overlay_on())
        return;
    count("overlay.own", &hits_own);
    if (logged_own < LOGGED_OWN && oa->ObjectName->Length > 14
            && same(oa->ObjectName->Buffer + d_rest(oa), "\\data files\\")) {
        logged_own++;
        tes3x_log_raw("overlay.own_path ", 17);
        tes3x_log_raw(oa->ObjectName->Buffer, oa->ObjectName->Length);
        tes3x_log_raw("\n", 1);
    }
    note_archive(h, oa->ObjectName->Buffer, oa->ObjectName->Length);
}

static int redirect(OBJECT_ATTRIBUTES *oa, OBJECT_ATTRIBUTES *alt, ANSI_STRING *name, char *buf)
{
    u32 rest = d_rest(oa), n, i;
    const char *s;

    if (!rest || !overlay_on())
        return 0;
    s = oa->ObjectName->Buffer + rest;
    n = oa->ObjectName->Length - rest;
    if (base_len + n >= PATH_MAX)
        return 0;
    for (i = 0; i < base_len; i++)
        buf[i] = base[i];
    for (i = 0; i < n; i++)
        buf[base_len + i] = s[i];
    buf[base_len + n] = 0;
    tes3x_object_attributes(alt, name, buf);
    alt->Attributes = oa->Attributes;
    return 1;
}

static int missing(u32 status)
{
    return status == STATUS_OBJECT_NAME_NOT_FOUND || status == STATUS_OBJECT_PATH_NOT_FOUND
        || status == STATUS_NO_SUCH_FILE;
}

static int read_only(u32 access, u32 options)
{
    return !(access & WRITE_ACCESS) && !(options & FILE_DELETE_ON_CLOSE);
}

/* Lock-free: NtClose runs through here for every handle the title closes. */
static PAIR *pair_find(void *h)
{
    int i;

    for (i = 0; i < PAIRS; i++)
        if (pairs[i].build == h)
            return &pairs[i];
    return 0;
}

static int pair_add(void *build, void *base_dir, OBJECT_ATTRIBUTES *oa)
{
    PAIR *p = 0;
    u32 i, n = oa->ObjectName->Length;

    if (n >= sizeof(p->dir) - 1)
        return 0;
    for (i = 0; i < PAIRS && !p; i++)
        if (__sync_bool_compare_and_swap(&pairs[i].build, (void *)0, PAIR_CLAIMED))
            p = &pairs[i];
    if (!p)
        return 0;
    p->base = base_dir;
    p->phase = 0;
    p->has_mask = 0;
    for (i = 0; i < n; i++)
        p->dir[i] = oa->ObjectName->Buffer[i];
    if (n && p->dir[n - 1] != '\\')
        p->dir[n++] = '\\';
    p->dir[n] = 0;
    p->dir_len = n;
    __sync_synchronize();
    p->build = build;
    return 1;
}

static void pair_free(PAIR *p)
{
    __sync_synchronize();
    p->build = 0;
}

static u32 __stdcall overlay_create(void **h, u32 access, OBJECT_ATTRIBUTES *oa,
                                    IO_STATUS_BLOCK *iosb, u64 *alloc, u32 attrs, u32 share,
                                    u32 disposition, u32 options)
{
    u32 st = orig_create(h, access, oa, iosb, alloc, attrs, share, disposition, options);
    OBJECT_ATTRIBUTES alt;
    ANSI_STRING name;
    char buf[PATH_MAX];

    if (missing(st) && disposition == FILE_OPEN && read_only(access, options)
            && redirect(oa, &alt, &name, buf)
            && orig_create(h, access, &alt, iosb, alloc, attrs, share, disposition, options) == 0) {
        count("overlay.create", &hits_open);
        log_path("overlay.path ", buf, name.Length);
        note_archive(*h, buf, name.Length);
        return 0;
    }
    if (st == 0 && read_only(access, options))
        note_own(*h, oa, options);
    return st;
}

static u32 __stdcall overlay_open(void **h, u32 access, OBJECT_ATTRIBUTES *oa,
                                  IO_STATUS_BLOCK *iosb, u32 share, u32 options)
{
    u32 st = orig_open(h, access, oa, iosb, share, options);
    OBJECT_ATTRIBUTES alt;
    ANSI_STRING name;
    IO_STATUS_BLOCK alt_iosb;
    char buf[PATH_MAX];
    void *base_dir = 0;

    if (!read_only(access, options) || !redirect(oa, &alt, &name, buf))
        return st;
    if (missing(st)) {
        if (orig_open(h, access, &alt, iosb, share, options) != 0)
            return st;
        count("overlay.open", &hits_open);
        log_path("overlay.path ", buf, name.Length);
        note_archive(*h, buf, name.Length);
        return 0;
    }
    if (st == 0)
        note_own(*h, oa, options);
    if (st == 0 && (options & FILE_DIRECTORY_FILE)
            && orig_open(&base_dir, access, &alt, &alt_iosb, share, options) == 0) {
        if (pair_add(*h, base_dir, oa)) {
            count("overlay.dir", &hits_dirs);
        } else {
            orig_close(base_dir);
            tes3x_log("overlay.pairs_full", PAIRS);
        }
    }
    return st;
}

static u32 __stdcall overlay_attrs(OBJECT_ATTRIBUTES *oa, FILE_NETWORK_OPEN_INFORMATION *info)
{
    u32 st = orig_attrs(oa, info);
    OBJECT_ATTRIBUTES alt;
    ANSI_STRING name;
    char buf[PATH_MAX];

    if (missing(st) && redirect(oa, &alt, &name, buf) && orig_attrs(&alt, info) == 0) {
        count("overlay.attrs", &hits_attrs);
        return 0;
    }
    return st;
}

/* Whether the entry the base folder returned also exists in the build folder. */
static int in_build(PAIR *p, const u8 *info, u32 cls)
{
    FILE_NETWORK_OPEN_INFORMATION st;
    OBJECT_ATTRIBUTES oa;
    ANSI_STRING name;
    char buf[PATH_MAX];
    u32 n, i;

    if (cls != FileDirectoryInformation)
        return 0;
    n = *(const u32 *)(info + 0x3C);
    if (p->dir_len + n >= PATH_MAX)
        return 0;
    for (i = 0; i < p->dir_len; i++)
        buf[i] = p->dir[i];
    for (i = 0; i < n; i++)
        buf[p->dir_len + i] = (char)info[0x40 + i];
    buf[p->dir_len + n] = 0;
    tes3x_dos_attributes(&oa, &name, buf);
    return orig_attrs(&oa, &st) == 0;
}

static u32 __stdcall overlay_query_dir(void *h, void *event, void *apc, void *ctx,
                                       IO_STATUS_BLOCK *iosb, void *info, u32 len, u32 cls,
                                       ANSI_STRING *mask, u32 restart)
{
    PAIR *p = pair_find(h);
    ANSI_STRING *base_mask;
    u32 st, i;

    if (!p)
        return orig_query_dir(h, event, apc, ctx, iosb, info, len, cls, mask, restart);
    if (mask && p->phase == 0 && mask->Length < sizeof(p->mask_buf)) {
        for (i = 0; i < mask->Length; i++)
            p->mask_buf[i] = mask->Buffer[i];
        p->mask.Buffer = p->mask_buf;
        p->mask.Length = p->mask.MaximumLength = mask->Length;
        p->has_mask = 1;
    }
    if (p->phase == 0) {
        st = orig_query_dir(h, event, apc, ctx, iosb, info, len, cls, mask, restart);
        if (st != STATUS_NO_MORE_FILES && st != STATUS_NO_SUCH_FILE)
            return st;
        p->phase = 1;
    }
    for (;;) {
        base_mask = p->phase == 1 && p->has_mask ? &p->mask : 0;
        st = orig_query_dir(p->base, event, apc, ctx, iosb, info, len, cls, base_mask,
                            p->phase == 1 ? restart : 0);
        p->phase = 2;
        if (st != 0 || !in_build(p, (const u8 *)info, cls))
            return st;
        count("overlay.dup", &hits_dups);
    }
}

static u32 __stdcall overlay_close(void *h)
{
    PAIR *p = h ? pair_find(h) : 0;
    int i;

    if (p) {
        orig_close(p->base);
        pair_free(p);
    }
    for (i = 0; h && i < ARCHIVES; i++)
        if (archives[i] == h)
            archives[i] = 0;
    return orig_close(h);
}

static u32 __stdcall overlay_read(void *h, void *event, void *apc, void *ctx,
                                  IO_STATUS_BLOCK *iosb, void *buf, u32 len, u64 *offset)
{
    int i;

    for (i = 0; h && i < ARCHIVES; i++)
        if (archives[i] == h) {
            count(archive_tags[i], &archive_reads[i]);
            break;
        }
    return orig_read(h, event, apc, ctx, iosb, buf, len, offset);
}

/* "F:\Games\X" or "\Device\...\X" into a device path without a trailing backslash. */
static int set_base(const char *s)
{
    static const char dev[] = "\\Device\\Harddisk0\\Partition";
    char part = 0;
    u32 n = 0, i;

    while (*s == ' ' || *s == '\t')
        s++;
    if (s[0] && s[1] == ':' && s[2] == '\\') {
        switch (s[0] | 0x20) {
        case 'c': part = '2'; break;
        case 'e': part = '1'; break;
        case 'f': part = '6'; break;
        case 'g': part = '7'; break;
        default: return 0;
        }
        for (i = 0; dev[i]; i++)
            base[n++] = dev[i];
        base[n++] = part;
        s += 2;
    } else if (s[0] != '\\') {
        return 0;
    }
    while (*s && n < sizeof(base) - 1)
        base[n++] = *s++;
    if (*s)
        return 0;
    while (n && (base[n - 1] == '\\' || base[n - 1] == ' ' || base[n - 1] == '\t'))
        n--;
    base[n] = 0;
    base_len = n;
    return n > 0;
}

static int same(const char *a, const char *b)
{
    while (*b)
        if ((*a++ | 0x20) != (*b++ | 0x20))
            return 0;
    return 1;
}

/* One ini line: track the section, and copy KEY's value when in [Xbox]. */
static int ini_line(char *line, int *in_xbox, const char *key, char *out, u32 size)
{
    u32 n = tes3x_strlen(key), i;
    char *v;

    while (*line == ' ' || *line == '\t')
        line++;
    if (*line == '[') {
        *in_xbox = same(line, "[xbox]");
        return 0;
    }
    if (!*in_xbox || !same(line, key))
        return 0;
    for (v = line + n; *v == ' ' || *v == '\t'; v++)
        ;
    if (*v++ != '=')
        return 0;
    for (i = 0; v[i] && i < size - 1; i++)
        out[i] = v[i];
    out[i] = 0;
    return !v[i];
}

/* The engine's ini reader is not usable this early, so D:\Morrowind.ini is scanned directly. */
static int ini_text(const char *key, char *out, u32 size)
{
    static char path[] = "D:\\Morrowind.ini";
    OBJECT_ATTRIBUTES oa;
    ANSI_STRING name;
    IO_STATUS_BLOCK iosb;
    char chunk[512], line[300];
    u32 n = 0, i, got;
    u64 pos = 0;
    int in_xbox = 0, found = 0;
    void *h = 0;

    tes3x_dos_attributes(&oa, &name, path);
    if (orig_create(&h, GENERIC_READ | SYNCHRONIZE, &oa, &iosb, 0, FILE_ATTRIBUTE_NORMAL,
                    FILE_SHARE_READ, FILE_OPEN, FILE_SYNCHRONOUS_IO_NONALERT) != 0)
        return 0;
    /* FATX requires an explicit read offset. */
    while (!found && KFN(THUNK_NtReadFile, fn_NtReadFile)(h, 0, 0, 0, &iosb, chunk,
                                                          sizeof(chunk), &pos) == 0) {
        got = iosb.Information;
        if (!got)
            break;
        pos += got;
        for (i = 0; i < got && !found; i++) {
            char c = chunk[i];
            if (c == '\n' || c == '\r') {
                line[n] = 0;
                found = ini_line(line, &in_xbox, key, out, size);
                n = 0;
            } else if (n < sizeof(line) - 1) {
                line[n++] = c;
            }
        }
    }
    if (!found && n) {
        line[n] = 0;
        found = ini_line(line, &in_xbox, key, out, size);
    }
    orig_close(h);
    return found;
}

static void configure(void)
{
    FILE_NETWORK_OPEN_INFORMATION info;
    OBJECT_ATTRIBUTES oa;
    ANSI_STRING name;
    char value[sizeof(base)];
    u32 st;

    if (!ini_text("OverlayBase", value, sizeof(value)) || !value[0]) {
        tes3x_log("overlay.off", 0);
        state = 3;
        return;
    }
    if (!set_base(value)) {
        tes3x_log("overlay.bad_base", 0);
        state = 3;
        return;
    }
    tes3x_object_attributes(&oa, &name, base);
    st = orig_attrs(&oa, &info);
    if (st != 0) {
        tes3x_log_hex("overlay.base_missing", st);
        state = 3;
        return;
    }
    tes3x_log("overlay.on", base_len);
    log_path("overlay.base ", base, base_len);
    state = 2;
}

static void swap(u32 slot, void *hook, void *save)
{
    *(u32 *)save = *(u32 *)slot;
    *(u32 *)slot = (u32)hook;
}

void tes3x_overlay_init(void)
{
    if (!tes3x_overlay_installed)
        return;
    /* NtClose goes first so no directory pair exists before its close is seen. */
    swap(THUNK_NtClose, (void *)overlay_close, &orig_close);
    swap(THUNK_NtQueryDirectoryFile, (void *)overlay_query_dir, &orig_query_dir);
    swap(THUNK_NtQueryFullAttributesFile, (void *)overlay_attrs, &orig_attrs);
    swap(THUNK_NtCreateFile, (void *)overlay_create, &orig_create);
    swap(THUNK_NtOpenFile, (void *)overlay_open, &orig_open);
    swap(THUNK_NtReadFile, (void *)overlay_read, &orig_read);
    tes3x_log("overlay.installed", 1);
}
