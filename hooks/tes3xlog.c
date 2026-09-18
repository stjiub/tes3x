/* Timestamped log to T:\tes3xlog.txt, for measuring on hardware.
 *
 * DbgPrint only reaches a debug kit, so anything we want to read back off a real console
 * has to land in a file. Everything here goes through the host XBE's kernel thunks; there
 * is no CRT, so the formatting is hand-rolled.
 */

#include "tes3x_thunks.h"
#include "tes3xlog.h"

#define KFN(slot, type) (*(type *)(slot))

typedef struct {
    unsigned short Length;
    unsigned short MaximumLength;
    char *Buffer;
} ANSI_STRING;

/* Xbox OBJECT_ATTRIBUTES has no Length field, unlike desktop NT. */
typedef struct {
    void *RootDirectory;
    ANSI_STRING *ObjectName;
    u32 Attributes;
} OBJECT_ATTRIBUTES;

typedef struct {
    u32 Status;
    u32 Information;
} IO_STATUS_BLOCK;

typedef u32(__stdcall *fn_NtCreateFile)(void **, u32, OBJECT_ATTRIBUTES *, IO_STATUS_BLOCK *,
                                        u64 *, u32, u32, u32, u32);
typedef u32(__stdcall *fn_NtWriteFile)(void *, void *, void *, void *, IO_STATUS_BLOCK *,
                                       const void *, u32, u64 *);
typedef u32(__stdcall *fn_NtClose)(void *);
typedef void(__stdcall *fn_KeQuerySystemTime)(u64 *);

#define NtCreateFile KFN(THUNK_NtCreateFile, fn_NtCreateFile)
#define NtWriteFile KFN(THUNK_NtWriteFile, fn_NtWriteFile)
#define NtClose KFN(THUNK_NtClose, fn_NtClose)
#define KeQuerySystemTime KFN(THUNK_KeQuerySystemTime, fn_KeQuerySystemTime)

#define GENERIC_WRITE 0x40000000u
#define SYNCHRONIZE 0x00100000u
#define FILE_ATTRIBUTE_NORMAL 0x80u
#define FILE_SHARE_READ 0x01u
#define FILE_OPEN_IF 3u
#define FILE_SYNCHRONOUS_IO_NONALERT 0x20u
#define FILE_APPEND_DATA 0x0004u

/* Device path, not "\??\T:". The T:/U:/Z: symlinks are created by XAPI process init, which
 * runs after the XBE entry point - so a hook placed at the entry would find T: missing.
 * Partition1 is E:, which the kernel has mounted before any title code runs. */
static char tes3x_path[] = "\\Device\\Harddisk0\\Partition1\\tes3xlog.txt";
static u64 tes3x_t0;

static u32 str_len(const char *s)
{
    u32 n = 0;
    while (s[n])
        n++;
    return n;
}

/* Writes right-to-left into the tail of buf; returns a pointer to the first digit. */
static char *u32_dec(u32 v, char *end)
{
    *--end = 0;
    do {
        *--end = (char)('0' + (v % 10));
        v /= 10;
    } while (v);
    return end;
}

/* Freestanding builds have no __aulldiv, so divide the 64-bit tick delta by hand.
 * The quotient truncates to 32 bits, which wraps after ~49 days of uptime. */
static u32 ticks_to_ms(u64 t)
{
    u64 rem = 0;
    u32 q = 0;
    u32 i;

    for (i = 0; i < 64; i++) {
        rem = (rem << 1) | ((t >> (63 - i)) & 1);
        q <<= 1;
        if (rem >= 10000) {
            rem -= 10000;
            q |= 1;
        }
    }
    return q;
}

/* 100ns system-time ticks since the first log call, rendered as milliseconds. */
static u32 tes3x_elapsed_ms(void)
{
    u64 now;
    KeQuerySystemTime(&now);
    if (!tes3x_t0) {
        tes3x_t0 = now;
        return 0;
    }
    return ticks_to_ms(now - tes3x_t0);
}

void tes3x_log(const char *tag, u32 value)
{
    char line[96];
    char num[16];
    u32 n = 0;
    const char *p;

    for (p = u32_dec(tes3x_elapsed_ms(), num + sizeof(num)); *p; p++)
        line[n++] = *p;
    line[n++] = ' ';
    line[n++] = 'm';
    line[n++] = 's';
    line[n++] = ' ';
    for (p = tag; *p && n < sizeof(line) - 20; p++)
        line[n++] = *p;
    line[n++] = ' ';
    for (p = u32_dec(value, num + sizeof(num)); *p; p++)
        line[n++] = *p;
    line[n++] = '\r';
    line[n++] = '\n';

    tes3x_log_raw(line, n);
}

void tes3x_log_raw(const char *buf, u32 len)
{
    ANSI_STRING name;
    OBJECT_ATTRIBUTES oa;
    IO_STATUS_BLOCK iosb;
    void *h = 0;

    name.Buffer = tes3x_path;
    name.Length = (unsigned short)str_len(tes3x_path);
    name.MaximumLength = name.Length;
    oa.RootDirectory = 0;
    oa.ObjectName = &name;
    oa.Attributes = 0x40; /* OBJ_CASE_INSENSITIVE */

    /* opened per call and appended: a crash mid-load must not cost us the log */
    if (NtCreateFile(&h, FILE_APPEND_DATA | SYNCHRONIZE, &oa, &iosb, 0,
                     FILE_ATTRIBUTE_NORMAL, FILE_SHARE_READ, FILE_OPEN_IF,
                     FILE_SYNCHRONOUS_IO_NONALERT) != 0)
        return;
    NtWriteFile(h, 0, 0, 0, &iosb, buf, len, 0);
    NtClose(h);
}
