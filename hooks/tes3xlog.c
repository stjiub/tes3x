/* Hardware log at E:\tes3xlog.txt.
 * Uses host XBE kernel thunks and a freestanding formatter.
 */

#include "tes3x_thunks.h"
#include "tes3xnt.h"
#include "tes3xlog.h"

#define NtCreateFile KFN(THUNK_NtCreateFile, fn_NtCreateFile)
#define NtWriteFile KFN(THUNK_NtWriteFile, fn_NtWriteFile)
#define NtQueryInformationFile KFN(THUNK_NtQueryInformationFile, fn_NtQueryInformationFile)
#define NtClose KFN(THUNK_NtClose, fn_NtClose)
#define KeQuerySystemTime KFN(THUNK_KeQuerySystemTime, fn_KeQuerySystemTime)

#define TES3X_LOG_MAX (512u * 1024u)
#define TES3X_LOG_CLUSTER (16u * 1024u)

static char tes3x_path[] = "\\Device\\Harddisk0\\Partition1\\tes3xlog.txt";
static u64 tes3x_t0;
static u32 tes3x_log_bytes;
static int tes3x_log_size_known;

static void tes3x_log_truncate(void)
{
    ANSI_STRING name;
    OBJECT_ATTRIBUTES oa;
    IO_STATUS_BLOCK iosb;
    void *h = 0;

    tes3x_object_attributes(&oa, &name, tes3x_path);
    if (NtCreateFile(&h, GENERIC_WRITE | SYNCHRONIZE, &oa, &iosb, 0,
                     FILE_ATTRIBUTE_NORMAL, FILE_SHARE_READ, FILE_OVERWRITE_IF,
                     FILE_SYNCHRONOUS_IO_NONALERT) == 0) {
        NtClose(h);
        tes3x_log_bytes = 0;
        tes3x_log_size_known = 1;
    }
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

static char *u32_hex(u32 v, char *end)
{
    static const char digits[] = "0123456789ABCDEF";
    int i;

    *--end = 0;
    for (i = 0; i < 8; i++) {
        *--end = digits[v & 0xF];
        v >>= 4;
    }
    *--end = 'x';
    *--end = '0';
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

void tes3x_log_hex(const char *tag, u32 value)
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
    for (p = u32_hex(value, num + sizeof(num)); *p; p++)
        line[n++] = *p;
    line[n++] = '\r';
    line[n++] = '\n';

    tes3x_log_raw(line, n);
}

void tes3x_log_hex3(const char *tag, u32 a, u32 b, u32 c)
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
    for (p = tag; *p && n < sizeof(line) - 38; p++)
        line[n++] = *p;
    line[n++] = ' ';
    for (p = u32_hex(a, num + sizeof(num)); *p; p++)
        line[n++] = *p;
    line[n++] = ' ';
    for (p = u32_hex(b, num + sizeof(num)); *p; p++)
        line[n++] = *p;
    line[n++] = ' ';
    for (p = u32_hex(c, num + sizeof(num)); *p; p++)
        line[n++] = *p;
    line[n++] = '\r';
    line[n++] = '\n';

    tes3x_log_raw(line, n);
}

void tes3x_log_prepare(void)
{
    ANSI_STRING name;
    OBJECT_ATTRIBUTES oa;
    IO_STATUS_BLOCK iosb;
    FILE_NETWORK_OPEN_INFORMATION st;
    void *h = 0;

    tes3x_object_attributes(&oa, &name, tes3x_path);
    if (NtCreateFile(&h, GENERIC_READ | FILE_APPEND_DATA | SYNCHRONIZE, &oa, &iosb, 0,
                     FILE_ATTRIBUTE_NORMAL, FILE_SHARE_READ, FILE_OPEN_IF,
                     FILE_SYNCHRONOUS_IO_NONALERT) != 0)
        return;
    if (NtQueryInformationFile(h, &iosb, &st, sizeof(st), FileNetworkOpenInformation) == 0) {
        tes3x_log_bytes = (u32)st.EndOfFile;
        tes3x_log_size_known = 1;
    }
    NtClose(h);

    if (tes3x_log_size_known && tes3x_log_bytes >= TES3X_LOG_MAX)
        tes3x_log_truncate();
}

void tes3x_log_raw(const char *buf, u32 len)
{
    ANSI_STRING name;
    OBJECT_ATTRIBUTES oa;
    IO_STATUS_BLOCK iosb;
    u64 append;
    void *h = 0;
    u32 done = 0, status;

    if (!tes3x_log_size_known)
        tes3x_log_prepare();
    if (tes3x_log_size_known && tes3x_log_bytes + len > TES3X_LOG_MAX)
        tes3x_log_truncate();

    tes3x_object_attributes(&oa, &name, tes3x_path);

    /* Open per call so a crash does not lose buffered entries. */
    status = NtCreateFile(&h, FILE_APPEND_DATA | SYNCHRONIZE, &oa, &iosb, 0,
                     FILE_ATTRIBUTE_NORMAL, FILE_SHARE_READ, FILE_OPEN_IF,
                     FILE_SYNCHRONOUS_IO_NONALERT);
    if (status != 0)
        return;
    append = FILE_WRITE_TO_END_OF_FILE;
    /* FATX requires an explicit append offset. A write spanning its 16 KB allocation boundary
     * can stall, so end one write at the boundary and start the next there. */
    while (done < len) {
        u32 chunk = len - done;

        if (tes3x_log_size_known) {
            u32 to_boundary = TES3X_LOG_CLUSTER -
                              (tes3x_log_bytes & (TES3X_LOG_CLUSTER - 1));
            if (chunk > to_boundary)
                chunk = to_boundary;
        }
        if (NtWriteFile(h, 0, 0, 0, &iosb, (void *)(buf + done), chunk, &append) != 0 ||
            iosb.Information != chunk)
            break;
        done += chunk;
        if (tes3x_log_size_known)
            tes3x_log_bytes += chunk;
    }
    NtClose(h);
}
