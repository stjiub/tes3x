/* Load archives from tes3xarch.txt after Morrowind.bsa.
 * Archive::Load prepends entries, so later list lines win.
 */

#include "tes3x_thunks.h"
#include "tes3xnt.h"
#include "tes3xlog.h"

#ifndef TES3X_ARCHIVE_LOAD
#error "define TES3X_ARCHIVE_LOAD to the VA of Archive::Load"
#endif

#define TES3X_STR_(x) #x
#define TES3X_STR(x) TES3X_STR_(x)

#define NtCreateFile KFN(THUNK_NtCreateFile, fn_NtCreateFile)
#define NtReadFile KFN(THUNK_NtReadFile, fn_NtReadFile)
#define NtClose KFN(THUNK_NtClose, fn_NtClose)
#define MmAllocateSystemMemory KFN(THUNK_MmAllocateSystemMemory, fn_MmAllocateSystemMemory)
#define MmFreeSystemMemory KFN(THUNK_MmFreeSystemMemory, fn_MmFreeSystemMemory)
#define IoCreateSymbolicLink KFN(THUNK_IoCreateSymbolicLink, fn_IoCreateSymbolicLink)

typedef u32(__stdcall *fn_IoCreateSymbolicLink)(ANSI_STRING *, ANSI_STRING *);

typedef unsigned char(__attribute__((thiscall)) * fn_archive_load)(void *, const char *);

#define SCRATCH 4096
#define LIST_NAME "tes3xarch.txt"
#define ARCHIVE_NEXT 0x150 /* the list pointer inside each archive object */

/* Direct structural check that Load actually linked something: walk the manager's chain. */
static u32 chain_len(void *self)
{
    void *p = *(void **)self;
    u32 n = 0;
    while (p && n < 64) {
        n++;
        p = *(void **)((char *)p + ARCHIVE_NEXT);
    }
    return n;
}

static u32 read_all(char *path, char *dst, u32 cap)
{
    ANSI_STRING name;
    OBJECT_ATTRIBUTES oa;
    IO_STATUS_BLOCK iosb;
    u64 zero = 0;
    void *h = 0;
    u32 status;

    tes3x_dos_attributes(&oa, &name, path);
    status = NtCreateFile(&h, GENERIC_READ | SYNCHRONIZE, &oa, &iosb, 0, FILE_ATTRIBUTE_NORMAL,
                          FILE_SHARE_READ, FILE_OPEN, FILE_SYNCHRONOUS_IO_NONALERT);
    if (status != 0) {
        tes3x_log("arch.open_fail", status);
        return 0;
    }
    iosb.Information = 0;
    /* FATX requires an explicit read offset. */
    status = NtReadFile(h, 0, 0, 0, &iosb, dst, cap, &zero);
    NtClose(h);
    if (status != 0)
        tes3x_log("arch.read_status", status);
    return iosb.Information;
}

/* Copies dir (already ending in a backslash) + name into dst, NUL terminated. */
static void join(char *dst, const char *dir, u32 dirlen, const char *name, u32 namelen)
{
    u32 i;
    for (i = 0; i < dirlen; i++)
        dst[i] = dir[i];
    for (i = 0; i < namelen; i++)
        dst[dirlen + i] = name[i];
    dst[dirlen + namelen] = 0;
}

/* A title maps only its own drives. The engine strips a leading backslash from any path
 * without a drive letter, so a hard-disk partition must be reached through its letter. */
static void map_drive(char letter)
{
    static char link[] = "\\??\\E:";
    static char target[] = "\\Device\\Harddisk0\\Partition1";
    static u8 mapped;
    ANSI_STRING l, t;
    u32 bit;
    char part;

    switch (letter | 0x20) {
    case 'c': part = '2'; break;
    case 'e': part = '1'; break;
    case 'f': part = '6'; break;
    case 'g': part = '7'; break;
    default: return;
    }
    bit = 1u << (part - '0');
    if (mapped & bit)
        return;
    mapped |= bit;
    link[4] = letter;
    target[sizeof(target) - 2] = part;
    l.Buffer = link;
    l.Length = l.MaximumLength = sizeof(link) - 1;
    t.Buffer = target;
    t.Length = t.MaximumLength = sizeof(target) - 1;
    /* fails harmlessly when the letter already exists */
    tes3x_log("arch.map", IoCreateSymbolicLink(&l, &t));
}

/* Copies an absolute list line into dst; 0 for a path relative to the archive folder. */
static u32 absolute(char *dst, const char *s, u32 len)
{
    u32 i;

    if (len < 3 || s[1] != ':' || s[2] != '\\')
        return 0;
    map_drive(s[0]);
    for (i = 0; i < len; i++)
        dst[i] = s[i];
    dst[len] = 0;
    return len;
}

void tes3x_archive_extra(void *self, const char *path)
{
    fn_archive_load load = (fn_archive_load)TES3X_ARCHIVE_LOAD;
    static const char list_name[] = LIST_NAME;
    char *buf, *full;
    u32 dirlen = 0, n, i, start, loaded = 0;

    for (i = 0; path[i]; i++)
        if (path[i] == '\\')
            dirlen = i + 1;
    if (!dirlen || dirlen > SCRATCH / 2)
        return;

    tes3x_log("arch.chain0", chain_len(self));
    tes3x_log("arch.dirlen", dirlen);
    buf = (char *)MmAllocateSystemMemory(SCRATCH * 2, PAGE_READWRITE);
    if (!buf) {
        tes3x_log("arch.no_buffer", 0);
        return;
    }
    full = buf + SCRATCH;

    join(full, path, dirlen, list_name, sizeof(list_name) - 1);
    n = read_all(full, buf, SCRATCH - 1);
    tes3x_log("arch.list_bytes", n);
    if (!n) {
        MmFreeSystemMemory(buf, SCRATCH * 2);
        return;
    }

    start = 0;
    for (i = 0; i <= n; i++) {
        char c = (i < n) ? buf[i] : '\n';
        u32 lo, hi;
        if (c != '\n' && c != '\r')
            continue;
        lo = start;
        hi = i;
        start = i + 1;
        while (lo < hi && (buf[lo] == ' ' || buf[lo] == '\t'))
            lo++;
        while (hi > lo && (buf[hi - 1] == ' ' || buf[hi - 1] == '\t'))
            hi--;
        if (lo == hi || buf[lo] == ';' || buf[lo] == '#')
            continue;
        if (dirlen + (hi - lo) >= SCRATCH - 64)
            continue;
        if (!absolute(full, buf + lo, hi - lo))
            join(full, path, dirlen, buf + lo, hi - lo);
        tes3x_log("arch.load", load(self, full));
        loaded++;
    }

    tes3x_log("arch.extra", loaded);
    tes3x_log("arch.chain", chain_len(self));
    MmFreeSystemMemory(buf, SCRATCH * 2);
}

/* __thiscall shim. Load retail first and preserve its return value. */
__attribute__((naked)) void tes3x_archive_hook(void)
{
    __asm__ volatile(
        "pushl %ebx\n\t"
        "movl 0x8(%esp), %ebx\n\t" /* the path argument */
        "pushl %ecx\n\t"           /* save this across the call */
        "pushl %ebx\n\t"
        "movl $" TES3X_STR(TES3X_ARCHIVE_LOAD) ", %eax\n\t"
        "call *%eax\n\t" /* pops its own argument */
        "popl %edx\n\t"
        "pushl %eax\n\t" /* preserve the original's result */
        "pushl %ebx\n\t"
        "pushl %edx\n\t"
        "call _tes3x_archive_extra\n\t"
        "addl $0x8, %esp\n\t"
        "popl %eax\n\t"
        "popl %ebx\n\t"
        "ret $0x4\n\t");
}
