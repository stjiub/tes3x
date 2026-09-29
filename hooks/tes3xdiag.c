/* INI-controlled diagnostics, an unhandled-exception record and a frame watchdog. */

#include "tes3x_thunks.h"
#include "tes3xnt.h"
#include "tes3xlog.h"
#include "tes3xdiag.h"
#ifdef TES3X_PROFILE
#include "tes3xprof.h"
#endif
#ifdef TES3X_HEAP
#include "tes3xheap.h"
#endif
#ifdef TES3X_MEM
#include "tes3xmem.h"
#endif
#ifdef TES3X_NET
void tes3x_net_frame(void);
#endif

#ifndef TES3X_INI_GET_STRING
#error "define TES3X_INI_GET_STRING to the VA of the ini string reader"
#endif
#ifndef TES3X_INI_PATH
#error "define TES3X_INI_PATH to the VA of the engine's ini filename string"
#endif
#ifndef TES3X_DIAG_UPDATE
#error "define TES3X_DIAG_UPDATE to Game::Update"
#endif
#ifndef TES3X_BUILD_ID
#define TES3X_BUILD_ID 0
#endif

#define TES3X_STR_(x) #x
#define TES3X_STR(x) TES3X_STR_(x)

/* Frame at which to fire each fault from the update loop; 0 leaves them console-only. */
#ifdef TES3X_DIAG_TEST_FAULTS
#ifndef TES3X_DIAG_TEST_HANG_AT
#define TES3X_DIAG_TEST_HANG_AT 0
#endif
#ifndef TES3X_DIAG_TEST_CRASH_AT
#define TES3X_DIAG_TEST_CRASH_AT 0
#endif
#endif

#define TES3X_DIAG_VERSION 1
#define TES3X_HANG_DEFAULT 60
#define TES3X_HANG_MIN 10
#define TES3X_HANG_MAX 600
#define TES3X_VERBOSE_FRAMES 3600

#define PsCreateSystemThreadEx KFN(THUNK_PsCreateSystemThreadEx, fn_PsCreateSystemThreadEx)
#define KeDelayExecutionThread KFN(THUNK_KeDelayExecutionThread, fn_KeDelayExecutionThread)
#define PsTerminateSystemThread KFN(THUNK_PsTerminateSystemThread, fn_PsTerminateSystemThread)
#define NtClose KFN(THUNK_NtClose, fn_NtClose)
#define MmQueryStatistics KFN(THUNK_MmQueryStatistics, fn_MmQueryStatistics)
#define MmQueryAddressProtect KFN(THUNK_MmQueryAddressProtect, fn_MmQueryAddressProtect)

typedef int(__cdecl *fn_ini_get_string)(const char *, const char *, const char *,
                                        char *, int, const char *);
typedef u32(__stdcall *fn_PsCreateSystemThreadEx)(void **, u32, u32, u32, void **,
                                                  void(__stdcall *)(void *), void *,
                                                  unsigned char, unsigned char, void *);
typedef u32(__stdcall *fn_KeDelayExecutionThread)(u32, unsigned char, long long *);
typedef void(__stdcall *fn_PsTerminateSystemThread)(u32);
typedef u32(__stdcall *fn_MmQueryStatistics)(void *);
typedef u32(__stdcall *fn_MmQueryAddressProtect)(void *);

typedef struct {
    u32 Length;
    u32 TotalPhysicalPages;
    u32 AvailablePages;
    u32 VirtualMemoryBytesCommitted;
    u32 VirtualMemoryBytesReserved;
    u32 CachePagesCommitted;
    u32 PoolPagesCommitted;
    u32 StackPagesCommitted;
    u32 ImagePagesCommitted;
} MM_STATISTICS;

typedef struct {
    u32 ExceptionCode;
    u32 ExceptionFlags;
    void *ExceptionRecord;
    void *ExceptionAddress;
    u32 NumberParameters;
    u32 ExceptionInformation[15];
} EXCEPTION_RECORD;

/* Xbox CONTEXT has a 516-byte floating-point save block before the integer registers. */
typedef struct {
    u32 ContextFlags;
    unsigned char FloatSave[516];
    u32 Edi;
    u32 Esi;
    u32 Ebx;
    u32 Edx;
    u32 Ecx;
    u32 Eax;
    u32 Ebp;
    u32 Eip;
    u32 SegCs;
    u32 EFlags;
    u32 Esp;
    u32 SegSs;
} __attribute__((packed)) CONTEXT;

extern u64 tes3x_boot_time;

volatile u32 tes3x_diag_installed;
volatile u32 tes3x_patch_mask;

static volatile u32 diag_heartbeat;
static volatile u32 diag_last_code;
static volatile u32 diag_last_value;
static volatile u32 diag_stall_seconds;
static volatile int diag_stalled;
static int diag_ready;
static int diag_level;
static int diag_watchdog;
static u32 diag_timeout = TES3X_HANG_DEFAULT;
static int diag_crash_written;
static int diag_watchdog_started;

static void start_watchdog(void);
int __cdecl tes3x_exception_handler(EXCEPTION_RECORD *, void *, CONTEXT *, void *);

static u32 free_kb(void)
{
    MM_STATISTICS st;
    st.Length = sizeof(st);
    if (MmQueryStatistics(&st) != 0)
        return 0;
    return st.AvailablePages * 4;
}

static int parse_uint(const char *s, int dflt)
{
    int v = 0;
    int any = 0;

    while (*s == ' ' || *s == '\t')
        s++;
    while (*s >= '0' && *s <= '9') {
        any = 1;
        v = v * 10 + (*s++ - '0');
    }
    return any ? v : dflt;
}

static int ini_uint(const char *key, int dflt)
{
    fn_ini_get_string get = (fn_ini_get_string)TES3X_INI_GET_STRING;
    char buf[24];
    int i;

    for (i = 0; i < (int)sizeof(buf); i++)
        buf[i] = 0;
    get("Xbox", key, "", buf, (int)sizeof(buf) - 1, (const char *)TES3X_INI_PATH);
    return parse_uint(buf, dflt);
}

static void snapshot(const char *reason)
{
    if (diag_level <= 0)
        return;
    tes3x_log(reason, diag_heartbeat);
    tes3x_log("diag.last_code", diag_last_code);
    tes3x_log_hex("diag.last_value", diag_last_value);
    tes3x_log("diag.free_kb", free_kb());
}

void tes3x_diag_snapshot(void)
{
    snapshot("diag.snapshot");
}

static int text_equal(const char *a, const char *b)
{
    for (;;) {
        char ca = *a++, cb = *b++;
        if (ca >= 'A' && ca <= 'Z')
            ca += 'a' - 'A';
        if (cb >= 'A' && cb <= 'Z')
            cb += 'a' - 'A';
        if (ca != cb)
            return 0;
        if (!ca)
            return 1;
    }
}

#ifdef TES3X_DIAG_TEST_FAULTS
/* Stall the update loop for longer than any valid HangTimeoutSeconds. */
static void test_hang(void)
{
    long long delay = -150000000ll;

    tes3x_log("diag.test_hang", 15);
    KeDelayExecutionThread(0, 0, &delay);
    tes3x_log("diag.test_hang_done", 15);
}

typedef struct seh_registration {
    struct seh_registration *next;
    void *handler;
} SEH_REGISTRATION;

/* Whether the update loop's thread can reach the record installed at the XBE entry point. */
static void test_seh_chain(void)
{
    SEH_REGISTRATION *entry;
    u32 depth = 0;

    __asm__ volatile("movl %%fs:0, %0" : "=r"(entry));
    for (; entry && entry != (SEH_REGISTRATION *)0xFFFFFFFF && depth < 16; depth++) {
        tes3x_log_hex("diag.seh", (u32)(unsigned int)entry->handler);
        if (entry->handler == (void *)tes3x_exception_handler)
            tes3x_log("diag.seh_ours", depth);
        entry = entry->next;
    }
    tes3x_log("diag.seh_depth", depth);
}

/* Write to the first address the kernel reports as unmapped, so the fault is a real one. */
static void test_crash(void)
{
    static void *const candidates[] = {(void *)0, (void *)0x7F000000, (void *)0xFFFFFFF0};
    u32 i;

    test_seh_chain();
    for (i = 0; i < sizeof(candidates) / sizeof(candidates[0]); i++) {
        u32 protect = MmQueryAddressProtect(candidates[i]);

        tes3x_log_hex("diag.test_target", (u32)(unsigned int)candidates[i]);
        tes3x_log_hex("diag.test_protect", protect);
        if (protect)
            continue;
        tes3x_log("diag.test_crash", 1);
        *(volatile u32 *)candidates[i] = 0x54583344;
        tes3x_log("diag.test_survived", 1);
    }
}
#endif

int tes3x_diag_command(const char *text)
{
    int level;

#ifdef TES3X_DIAG_TEST_FAULTS
    if (text_equal(text, "tes3xdiag hang")) {
        test_hang();
        return 1;
    }
    if (text_equal(text, "tes3xdiag crash")) {
        test_crash();
        return 1;
    }
#endif
    if (text_equal(text, "tes3xdiag")) {
        tes3x_diag_snapshot();
        return 1;
    }
    if (!text_equal(text, "tes3xdiag 0") && !text_equal(text, "tes3xdiag 1") &&
        !text_equal(text, "tes3xdiag 2"))
        return 0;
    level = text[10] - '0';
    diag_level = level;
    tes3x_log("diag.runtime_level", (u32)level);
    if (level > 0 && diag_watchdog && !diag_watchdog_started)
        start_watchdog();
    return 1;
}

void tes3x_diag_note(u32 code, u32 value)
{
    diag_last_code = code;
    diag_last_value = value;
    if (diag_level >= 2) {
        tes3x_log("diag.note", code);
        tes3x_log_hex("diag.note_value", value);
    }
}

static void __stdcall watchdog_thread(void *unused)
{
    u32 last = diag_heartbeat;
    long long one_second = -10000000ll;
    (void)unused;

    for (;;) {
        KeDelayExecutionThread(0, 0, &one_second);
        if (!diag_ready || diag_level <= 0 || !diag_watchdog) {
            last = diag_heartbeat;
            diag_stall_seconds = 0;
            diag_stalled = 0;
            continue;
        }
        if (diag_heartbeat != last) {
            last = diag_heartbeat;
            diag_stall_seconds = 0;
            continue;
        }
        if (diag_stall_seconds < 0xFFFFFFFFu)
            diag_stall_seconds++;
        if (!diag_stalled && diag_stall_seconds >= diag_timeout) {
            diag_stalled = 1;
            snapshot("hang.detected");
            tes3x_log("hang.seconds", diag_stall_seconds);
        }
    }
}

static void __stdcall watchdog_system(void(__stdcall *start)(void *), void *context)
{
    start(context);
    PsTerminateSystemThread(0);
}

static void start_watchdog(void)
{
    void *h = 0;
    u32 status;

    status = PsCreateSystemThreadEx(&h, 0, 0x4000, 0, 0, watchdog_thread, 0, 0, 0,
                                    (void *)watchdog_system);
    if (status == 0) {
        NtClose(h);
        diag_watchdog_started = 1;
        tes3x_log("diag.watchdog", diag_timeout);
    } else {
        tes3x_log_hex("diag.watchdog_fail", status);
        diag_watchdog = 0;
    }
}

static void load_config(void)
{
    int enabled = ini_uint("Diagnostics", 0);
    int level = ini_uint("DiagnosticsLevel", enabled > 0 ? enabled : 1);
    int timeout;

    if (!enabled)
        level = 0;
    if (level < 0)
        level = 0;
    if (level > 2)
        level = 2;
    diag_level = level;
    diag_watchdog = ini_uint("HangWatchdog", 1) != 0;
    timeout = ini_uint("HangTimeoutSeconds", TES3X_HANG_DEFAULT);
    if (timeout < TES3X_HANG_MIN)
        timeout = TES3X_HANG_MIN;
    if (timeout > TES3X_HANG_MAX)
        timeout = TES3X_HANG_MAX;
    diag_timeout = (u32)timeout;
    diag_ready = 1;

    if (diag_level > 0) {
        tes3x_log("diag.enabled", (u32)diag_level);
        tes3x_log("diag.version", TES3X_DIAG_VERSION);
        tes3x_log("diag.free_kb", free_kb());
#ifdef TES3X_DIAG_TEST_FAULTS
        tes3x_log("diag.test_faults", 1);
#endif
        if (diag_watchdog)
            start_watchdog();
    }
}

void tes3x_diag_init(void)
{
    tes3x_log_hex("diag.session", (u32)tes3x_boot_time);
    tes3x_log_hex("diag.build", TES3X_BUILD_ID);
    tes3x_log_hex("diag.patches", tes3x_patch_mask);
}

void tes3x_diag_tick(void)
{
#ifdef TES3X_PROFILE
    /* The sole once-per-frame call site, so tick to tick is one whole frame. */
    tes3x_prof_frame();
#endif
#ifdef TES3X_HEAP
    tes3x_heap_frame();
#endif
#ifdef TES3X_MEM
    tes3x_mem_frame();
#endif
#ifdef TES3X_NET
    tes3x_net_frame();
#endif
    diag_heartbeat++;
    if (!diag_ready)
        load_config();
    if (diag_stalled) {
        tes3x_log("hang.resumed", diag_stall_seconds);
        diag_stalled = 0;
        diag_stall_seconds = 0;
    }
    if (diag_level >= 2 && diag_heartbeat % TES3X_VERBOSE_FRAMES == 0)
        snapshot("diag.heartbeat");
#ifdef TES3X_DIAG_TEST_FAULTS
    if (TES3X_DIAG_TEST_HANG_AT && diag_heartbeat == TES3X_DIAG_TEST_HANG_AT)
        test_hang();
    if (TES3X_DIAG_TEST_CRASH_AT && diag_heartbeat == TES3X_DIAG_TEST_CRASH_AT)
        test_crash();
#endif
}

/* Innermost update-loop SEH handler. Leave normal engine handlers in control. */
int __cdecl tes3x_exception_handler(EXCEPTION_RECORD *record, void *registration,
                                    CONTEXT *context, void *dispatcher)
{
    u32 *stack;
    u32 i;
    (void)registration;
    (void)dispatcher;

    if (!record || !context || (record->ExceptionFlags & 0x66u))
        return 1;
    if ((diag_ready && diag_level <= 0) || diag_crash_written)
        return 1;
    diag_crash_written = 1;

    tes3x_log_hex("crash.code", record->ExceptionCode);
    tes3x_log_hex("crash.address", (u32)(unsigned int)record->ExceptionAddress);
    tes3x_log_hex("crash.eip", context->Eip);
    tes3x_log_hex("crash.esp", context->Esp);
    tes3x_log_hex("crash.ebp", context->Ebp);
    tes3x_log_hex("crash.eax", context->Eax);
    tes3x_log_hex("crash.ebx", context->Ebx);
    tes3x_log_hex("crash.ecx", context->Ecx);
    tes3x_log_hex("crash.edx", context->Edx);
    tes3x_log_hex("crash.esi", context->Esi);
    tes3x_log_hex("crash.edi", context->Edi);
    tes3x_log_hex("crash.eflags", context->EFlags);
    tes3x_log("crash.heartbeat", diag_heartbeat);
    tes3x_log("crash.last_code", diag_last_code);
    tes3x_log_hex("crash.last_value", diag_last_value);
    if (record->NumberParameters > 0)
        tes3x_log_hex("crash.info0", record->ExceptionInformation[0]);
    if (record->NumberParameters > 1)
        tes3x_log_hex("crash.info1", record->ExceptionInformation[1]);
    stack = (u32 *)context->Esp;
    for (i = 0; i < 16; i++) {
        char tag[] = "crash.stack00";
        u32 protect = MmQueryAddressProtect(stack + i);
        if (!protect || (protect & 0x101u))
            break;
        tag[11] = (char)('0' + i / 10);
        tag[12] = (char)('0' + i % 10);
        tes3x_log_hex(tag, stack[i]);
    }
    return 1;
}

/* Stand in for the sole Game::Update call and put our record first in this thread's chain. */
__attribute__((naked)) void tes3x_diag_update_hook(void)
{
    __asm__ volatile(
        "subl $12, %esp\n\t"
        "movl %fs:0, %eax\n\t"
        "movl %eax, (%esp)\n\t"
        "movl $_tes3x_exception_handler, 4(%esp)\n\t"
        "movl %ecx, 8(%esp)\n\t"
        "movl %esp, %eax\n\t"
        "movl %eax, %fs:0\n\t"
        "pushfl\n\t"
        "pushal\n\t"
        "call _tes3x_diag_tick\n\t"
        "popal\n\t"
        "popfl\n\t"
        "movl 8(%esp), %ecx\n\t"
        "pushl $1f\n\t"
        "pushl $" TES3X_STR(TES3X_DIAG_UPDATE) "\n\t"
        "ret\n\t"
        "1:\n\t"
        "pushl %eax\n\t"
        "movl 4(%esp), %eax\n\t"
        "movl %eax, %fs:0\n\t"
        "popl %eax\n\t"
        "addl $12, %esp\n\t"
        "ret\n\t");
}
