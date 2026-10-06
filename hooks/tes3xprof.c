/* RDTSC region timing for engine functions chosen at patch time.
 *
 * Each instrumented target owns a stub; the patcher redirects the target's direct call
 * sites to it and stores the target VA in tes3x_prof_target. The stub times the call by
 * swapping the caller's return address for tes3x_prof_exit, so it works for any calling
 * convention and needs no argument knowledge.
 *
 * Counters are dumped raw to E:\tes3xprof.bin and symbolized on PC.
 */

#include "tes3x_thunks.h"
#include "tes3xnt.h"
#include "tes3xlog.h"
#include "tes3xini.h"
#include "tes3xprof.h"

#ifndef TES3X_INI_GET_STRING
#error "define TES3X_INI_GET_STRING to the VA of the ini string reader"
#endif
#ifndef TES3X_INI_PATH
#error "define TES3X_INI_PATH to the VA of the engine's ini filename string"
#endif
#ifndef TES3X_BUILD_ID
#define TES3X_BUILD_ID 0
#endif
#define TES3X_PROF_SLOTS 16
#define TES3X_PROF_CALIB TES3X_PROF_SLOTS /* the extra slot, an empty instrumented call */
#define TES3X_PROF_TOTAL (TES3X_PROF_SLOTS + 1)
#define TES3X_PROF_DEPTH 64
#define TES3X_PROF_THREADS 4 /* the entry thread is not the one that loads or draws */
#define TES3X_PROF_BUCKETS 8
#define TES3X_PROF_CALIB_RUNS 1024 /* a power of two; the mean is a shift */
#define TES3X_PROF_CALIB_SHIFT 10
#define TES3X_PROF_MAGIC 0x50583354u /* "T3XP" */
#define TES3X_PROF_VERSION 2
#define TES3X_PROF_PREMENU_SECONDS 30
#define TES3X_PROF_SECONDS_MIN 5
#define TES3X_PROF_SECONDS_MAX 3600

#define TES3X_PROF_DUMP_CONSOLE 1
#define TES3X_PROF_DUMP_FRAME 2
#define TES3X_PROF_DUMP_INTERVAL 3
#define TES3X_PROF_DUMP_MARK 4
#define TES3X_PROF_DUMP_PREMENU 5
#define TES3X_PROF_DUMP_FIRST_RETURN 6
#define TES3X_PROF_DUMP_FIRST_ENTRY 7
#define TES3X_PROF_DUMP_SECOND_ENTRY 8

/* Nominal 733 MHz. Only the frame histogram uses it; the reader derives the real rate
 * from the system-time and TSC pairs in the header. */
#define PROF_CYCLES_PER_MS 733000u

#define NtCreateFile KFN(THUNK_NtCreateFile, fn_NtCreateFile)
#define NtWriteFile KFN(THUNK_NtWriteFile, fn_NtWriteFile)
#define NtClose KFN(THUNK_NtClose, fn_NtClose)
#define KeQuerySystemTime KFN(THUNK_KeQuerySystemTime, fn_KeQuerySystemTime)
#define MmQueryStatistics KFN(THUNK_MmQueryStatistics, fn_MmQueryStatistics)

typedef u32(__stdcall *fn_MmQueryStatistics)(void *);

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
    u32 calls;
    u32 foreign; /* calls from a thread that could not be given a shadow stack */
    u64 inclusive; /* including instrumented callees */
    u64 exclusive; /* with instrumented callee time subtracted */
    u64 lo;
    u64 hi;
} prof_slot;

typedef struct {
    u32 ret;   /* the caller's real return address */
    u32 slot;
    u32 frame; /* address of the return-address slot, so a dead frame is detectable */
    u32 self;  /* ecx at the call site */
    u64 start;
    u64 child; /* time spent in instrumented callees */
} prof_entry;

/* One shadow stack per thread: a frame's return address may only be restored on the
 * stack it was taken from, and several engine threads call instrumented code. */
typedef struct {
    volatile u32 base; /* the thread's stack base, 0 while unclaimed */
    u32 depth;
    prof_entry stack[TES3X_PROF_DEPTH];
} prof_ctx;

typedef struct {
    u32 magic;
    u32 version;
    u32 slots;
    u32 record;
    u64 session; /* system time at init, 100ns */
    u64 tsc_init;
    u64 dump_time;
    u64 tsc_now;
    u64 reset_time; /* start of the window the counters cover */
    u64 reset_tsc;
    u32 frames;
    u32 buckets;
    u64 frame_total;
    u64 frame_lo;
    u64 frame_hi;
    u32 hist[TES3X_PROF_BUCKETS];
    u32 overhead; /* mean cycles an empty instrumented call costs inside the window */
    u32 foreign;
    u32 overflow;
    u32 stale;
    u32 underflow;
    u32 depth_max;
    u32 threads;
    u32 patch_mask;
    u32 build_id;
    u32 reserved;
    u32 free_kb;
    u32 active;
    u32 active_record;
    u32 reason;
} prof_header;

typedef struct {
    u32 thread;
    u32 depth;
    u32 slot;
    u32 target;
    u32 caller;
    u32 self;
    u32 arg0;
    u32 arg1;
    u64 elapsed;
} prof_active;

extern volatile u32 tes3x_patch_mask;
extern volatile u32 tes3x_patch_mask_hi;

/* The patcher writes a target VA here; index TES3X_PROF_CALIB is set at init. */
u32 tes3x_prof_target[TES3X_PROF_TOTAL];
prof_slot tes3x_prof_slots[TES3X_PROF_TOTAL];

void tes3x_prof_exit(void);

static prof_ctx prof_threads[TES3X_PROF_THREADS];
static prof_active prof_active_frames[TES3X_PROF_THREADS * TES3X_PROF_DEPTH];
static u32 stat_foreign, stat_overflow, stat_stale, stat_underflow, stat_depth_max;
static u64 prof_session, prof_tsc_init, prof_reset_time, prof_reset_tsc;
static u64 frame_last, frame_total, frame_lo, frame_hi;
static u32 frame_count, frame_hist[TES3X_PROF_BUCKETS];
static u32 prof_dump_frames, prof_dump_seq;
static u32 prof_enters[TES3X_PROF_TOTAL];
static volatile u32 prof_dump_seconds = TES3X_PROF_PREMENU_SECONDS;
static volatile u32 prof_dump_lock;
static volatile u32 prof_premenu_dumped;
static u64 prof_dump_tsc;
static int prof_ready;
static char prof_path[] = "\\Device\\Harddisk0\\Partition1\\tes3xprof.bin";

static void prof_dump(u32 reason);

static inline u64 prof_tsc(void)
{
    u32 lo, hi;
    __asm__ volatile("rdtsc" : "=a"(lo), "=d"(hi));
    return ((u64)hi << 32) | lo;
}

/* fs selects the KPCR, whose first member is the thread's NT_TIB. StackBase is constant
 * per thread, which is all this needs. */
static inline u32 prof_stack_base(void)
{
    u32 v;
    __asm__ volatile("movl %%fs:4, %0" : "=r"(v));
    return v;
}

/* Returns the previous value; 0 means this thread took the slot. */
static inline u32 prof_claim(volatile u32 *slot, u32 want)
{
    u32 prev = 0;

    __asm__ volatile("lock cmpxchgl %2, %1"
                     : "+a"(prev), "+m"(*slot)
                     : "r"(want)
                     : "memory");
    return prev;
}

static u32 prof_free_kb(void)
{
    MM_STATISTICS st;

    st.Length = sizeof(st);
    if (MmQueryStatistics(&st) != 0)
        return 0;
    return st.AvailablePages * 4;
}

/* The context for this thread, claiming a free one on first sight. Two threads racing
 * for the same free slot would share a shadow stack and swap return addresses. */
static prof_ctx *prof_context(int claim)
{
    u32 base = prof_stack_base();
    u32 i;

    if (!base)
        return 0;
    for (i = 0; i < TES3X_PROF_THREADS; i++) {
        if (prof_threads[i].base == base)
            return &prof_threads[i];
    }
    if (!claim)
        return 0;
    for (i = 0; i < TES3X_PROF_THREADS; i++) {
        if (!prof_threads[i].base && prof_claim(&prof_threads[i].base, base) == 0)
            return &prof_threads[i];
    }
    return 0;
}

/* Entered from a stub with every register saved. ret_slot points at the caller's return
 * address on the stack; replacing it routes the return through tes3x_prof_exit. */
void __cdecl tes3x_prof_enter(u32 slot, u32 *ret_slot, u32 self)
{
    prof_ctx *c = prof_context(1);
    prof_entry *f;
    u32 entered;
    int checkpoint = 0;

    if (!c) {
        tes3x_prof_slots[slot].foreign++;
        stat_foreign++;
        return;
    }

    /* A frame at or below this one has already been left, by an exception unwind that
     * never reached tes3x_prof_exit. Live frames nest strictly downwards. */
    while (c->depth && c->stack[c->depth - 1].frame <= (u32)ret_slot) {
        c->depth--;
        stat_stale++;
    }
    if (c->depth >= TES3X_PROF_DEPTH) {
        stat_overflow++;
        return;
    }

    f = &c->stack[c->depth++];
    if (c->depth > stat_depth_max)
        stat_depth_max = c->depth;
    f->ret = *ret_slot;
    f->slot = slot;
    f->frame = (u32)ret_slot;
    f->self = self;
    f->child = 0;
    *ret_slot = (u32)(unsigned int)&tes3x_prof_exit;
    f->start = prof_tsc();

    if (slot != TES3X_PROF_CALIB) {
        entered = ++prof_enters[slot];
        /* Persist bounded active-frame snapshots before a malformed or oversized master can
         * enter a path that masks scheduler ticks. */
        if (prof_claim(&prof_premenu_dumped, 1) == 0) {
            prof_dump(TES3X_PROF_DUMP_PREMENU);
            checkpoint = 1;
        } else if (entered <= 2) {
            prof_dump(entered == 1 ? TES3X_PROF_DUMP_FIRST_ENTRY
                                   : TES3X_PROF_DUMP_SECOND_ENTRY);
            checkpoint = 1;
        } else if (prof_dump_seconds && prof_dump_tsc && f->start >= prof_dump_tsc &&
                 f->start - prof_dump_tsc >=
                     (u64)prof_dump_seconds * 1000u * PROF_CYCLES_PER_MS) {
            prof_dump(TES3X_PROF_DUMP_INTERVAL);
            checkpoint = 1;
        }
        if (checkpoint) {
            /* The checkpoint itself is instrumentation, not time spent in the target. */
            f->start = prof_tsc();
            f->child = 0;
        }
    }
}

/* Returns the caller's real return address. */
u32 __cdecl tes3x_prof_leave(void)
{
    u64 now = prof_tsc();
    prof_ctx *c = prof_context(0);
    prof_entry *f;
    prof_slot *s;
    u64 elapsed;
    u32 ret, slot;

    if (!c || !c->depth) {
        stat_underflow++;
        return 0;
    }
    f = &c->stack[--c->depth];
    ret = f->ret;
    slot = f->slot;
    elapsed = now - f->start;
    /* Slot counters are shared and unlocked. Two threads in the same target can lose an
     * update; a lock would cost more than the error it prevents. */
    s = &tes3x_prof_slots[slot];
    s->calls++;
    s->inclusive += elapsed;
    s->exclusive += elapsed - f->child;
    if (!s->lo || elapsed < s->lo)
        s->lo = elapsed;
    if (elapsed > s->hi)
        s->hi = elapsed;
    if (c->depth)
        c->stack[c->depth - 1].child += elapsed;
    /* One post-return checkpoint per selected target distinguishes a call that never
     * returns from a stall immediately after it, without turning every return into I/O. */
    if (slot != TES3X_PROF_CALIB && s->calls == 1)
        prof_dump(TES3X_PROF_DUMP_FIRST_RETURN);
    return ret;
}

/* eax and edx carry the callee's return value; st(0) is untouched. */
__attribute__((naked)) void tes3x_prof_exit(void)
{
    __asm__ volatile(
        "subl $4, %esp\n\t" /* room for the real return address */
        "pushl %eax\n\t"
        "pushl %ecx\n\t"
        "pushl %edx\n\t"
        "pushfl\n\t"
        "call _tes3x_prof_leave\n\t"
        "movl %eax, 16(%esp)\n\t"
        "popfl\n\t"
        "popl %edx\n\t"
        "popl %ecx\n\t"
        "popl %eax\n\t"
        "ret\n\t");
}

/* eax is scratch at a call site under every convention MSVC emits, so the lea is safe
 * inside the saved region and the indirect jump leaves the callee's stack untouched. */
#define PROF_STUB(k)                                             \
    __attribute__((naked)) static void tes3x_prof_stub_##k(void) \
    {                                                            \
        __asm__ volatile(                                        \
            "pushfl\n\t"                                         \
            "pushal\n\t"                                         \
            "leal 36(%esp), %eax\n\t"                            \
            "movl 24(%esp), %edx\n\t"                            \
            "pushl %edx\n\t"                                    \
            "pushl %eax\n\t"                                     \
            "pushl $" #k "\n\t"                                  \
            "call _tes3x_prof_enter\n\t"                         \
            "addl $12, %esp\n\t"                                 \
            "popal\n\t"                                          \
            "popfl\n\t"                                          \
            "jmpl *_tes3x_prof_target+" #k "*4\n\t");            \
    }

PROF_STUB(0)
PROF_STUB(1)
PROF_STUB(2)
PROF_STUB(3)
PROF_STUB(4)
PROF_STUB(5)
PROF_STUB(6)
PROF_STUB(7)
PROF_STUB(8)
PROF_STUB(9)
PROF_STUB(10)
PROF_STUB(11)
PROF_STUB(12)
PROF_STUB(13)
PROF_STUB(14)
PROF_STUB(15)
PROF_STUB(16)

/* The patcher reads a stub's VA from here and points the target's call sites at it. */
void *const tes3x_prof_stubs[TES3X_PROF_TOTAL] = {
    tes3x_prof_stub_0, tes3x_prof_stub_1, tes3x_prof_stub_2, tes3x_prof_stub_3,
    tes3x_prof_stub_4, tes3x_prof_stub_5, tes3x_prof_stub_6, tes3x_prof_stub_7,
    tes3x_prof_stub_8, tes3x_prof_stub_9, tes3x_prof_stub_10, tes3x_prof_stub_11,
    tes3x_prof_stub_12, tes3x_prof_stub_13, tes3x_prof_stub_14, tes3x_prof_stub_15,
    tes3x_prof_stub_16,
};

__attribute__((naked)) static void prof_calib_target(void)
{
    __asm__ volatile("ret\n\t");
}

/* Time an empty instrumented call, so a measured region can have the instrument's own
 * in-window cost subtracted. Re-run per dump: the figure is cache- and state-dependent. */
static u32 prof_calibrate(void)
{
    prof_slot *s = &tes3x_prof_slots[TES3X_PROF_CALIB];
    u32 i;

    s->calls = 0;
    s->inclusive = 0;
    s->exclusive = 0;
    s->lo = 0;
    s->hi = 0;
    for (i = 0; i < TES3X_PROF_CALIB_RUNS; i++)
        tes3x_prof_stub_16(); /* the stub for TES3X_PROF_CALIB */
    if (s->calls != TES3X_PROF_CALIB_RUNS)
        return 0;
    return (u32)(s->inclusive >> TES3X_PROF_CALIB_SHIFT);
}

static int prof_write(void *h, const void *buf, u32 len)
{
    IO_STATUS_BLOCK iosb;
    u64 append = FILE_WRITE_TO_END_OF_FILE;

    return NtWriteFile(h, 0, 0, 0, &iosb, buf, len, &append) == 0;
}

static u32 prof_snapshot_active(u64 now)
{
    u32 count = 0, i, j;

    for (i = 0; i < TES3X_PROF_THREADS; i++) {
        u32 depth = prof_threads[i].depth;

        if (!prof_threads[i].base)
            continue;
        if (depth > TES3X_PROF_DEPTH)
            depth = TES3X_PROF_DEPTH;
        for (j = 0; j < depth; j++) {
            prof_entry *frame = &prof_threads[i].stack[j];
            prof_active *active;
            u32 slot = frame->slot;
            u64 start = frame->start;

            /* Entry publishes depth before the remaining fields. A timer interrupting
             * those few instructions should omit the partial frame, not invent one. */
            if (slot >= TES3X_PROF_TOTAL || !start || start > now)
                continue;
            active = &prof_active_frames[count++];
            active->thread = prof_threads[i].base;
            active->depth = j;
            active->slot = slot;
            active->target = tes3x_prof_target[slot];
            active->caller = frame->ret;
            active->self = frame->self;
            active->arg0 = ((u32 *)frame->frame)[1];
            active->arg1 = ((u32 *)frame->frame)[2];
            active->elapsed = now - start;
        }
    }
    return count;
}

static void prof_dump(u32 reason)
{
    ANSI_STRING name;
    OBJECT_ATTRIBUTES oa;
    IO_STATUS_BLOCK iosb;
    prof_header hdr;
    void *h = 0;
    u32 i;

    /* A timer, a frame trigger and the console may coincide. Never make the loader
     * wait behind diagnostic I/O, and never interleave two append records. */
    if (prof_claim(&prof_dump_lock, 1) != 0) {
        tes3x_log("prof.dump_busy", reason);
        return;
    }

    hdr.overhead = prof_calibrate();
    hdr.magic = TES3X_PROF_MAGIC;
    hdr.version = TES3X_PROF_VERSION;
    hdr.slots = TES3X_PROF_TOTAL;
    hdr.record = sizeof(prof_slot);
    hdr.session = prof_session;
    hdr.tsc_init = prof_tsc_init;
    hdr.reset_time = prof_reset_time;
    hdr.reset_tsc = prof_reset_tsc;
    KeQuerySystemTime(&hdr.dump_time);
    hdr.tsc_now = prof_tsc();
    prof_dump_tsc = hdr.tsc_now;
    hdr.frames = frame_count;
    hdr.buckets = TES3X_PROF_BUCKETS;
    hdr.frame_total = frame_total;
    hdr.frame_lo = frame_lo;
    hdr.frame_hi = frame_hi;
    for (i = 0; i < TES3X_PROF_BUCKETS; i++)
        hdr.hist[i] = frame_hist[i];
    hdr.foreign = stat_foreign;
    hdr.overflow = stat_overflow;
    hdr.stale = stat_stale;
    hdr.underflow = stat_underflow;
    hdr.depth_max = stat_depth_max;
    hdr.threads = 0;
    for (i = 0; i < TES3X_PROF_THREADS; i++) {
        if (prof_threads[i].base)
            hdr.threads++;
    }
    hdr.patch_mask = tes3x_patch_mask;
    hdr.build_id = TES3X_BUILD_ID;
    hdr.reserved = tes3x_patch_mask_hi;
    hdr.free_kb = prof_free_kb();
    hdr.active = prof_snapshot_active(hdr.tsc_now);
    hdr.active_record = sizeof(prof_active);
    hdr.reason = reason;

    tes3x_object_attributes(&oa, &name, prof_path);
    /* The first dump of a session starts the file; later ones append blocks. */
    if (NtCreateFile(&h, GENERIC_WRITE | FILE_APPEND_DATA | SYNCHRONIZE, &oa, &iosb, 0,
                     FILE_ATTRIBUTE_NORMAL, FILE_SHARE_READ,
                     prof_dump_seq ? FILE_OPEN_IF : FILE_OVERWRITE_IF,
                     FILE_SYNCHRONOUS_IO_NONALERT) != 0) {
        tes3x_log("prof.dump_open_failed", prof_dump_seq);
        prof_dump_lock = 0;
        return;
    }
    if (prof_write(h, &hdr, sizeof(hdr)) &&
        prof_write(h, tes3x_prof_target, sizeof(tes3x_prof_target)) &&
        prof_write(h, tes3x_prof_slots, sizeof(tes3x_prof_slots)) &&
        (!hdr.active || prof_write(h, prof_active_frames, hdr.active * sizeof(prof_active))))
        prof_dump_seq++;
    NtClose(h);
    tes3x_log("prof.dump", prof_dump_seq);
    tes3x_log("prof.overhead_cycles", hdr.overhead);
    prof_dump_lock = 0;
}

static void prof_reset(void)
{
    u32 i;

    for (i = 0; i < TES3X_PROF_TOTAL; i++) {
        tes3x_prof_slots[i].calls = 0;
        tes3x_prof_slots[i].foreign = 0;
        tes3x_prof_slots[i].inclusive = 0;
        tes3x_prof_slots[i].exclusive = 0;
        tes3x_prof_slots[i].lo = 0;
        tes3x_prof_slots[i].hi = 0;
    }
    for (i = 0; i < TES3X_PROF_BUCKETS; i++)
        frame_hist[i] = 0;
    frame_count = 0;
    frame_total = 0;
    frame_lo = 0;
    frame_hi = 0;
    stat_foreign = 0;
    stat_overflow = 0;
    stat_stale = 0;
    stat_underflow = 0;
    stat_depth_max = 0;
    KeQuerySystemTime(&prof_reset_time);
    prof_reset_tsc = prof_tsc();
}

static int prof_uint(const char *key, int dflt)
{
    char buf[24];
    int v = 0, any = 0;
    const char *s = buf;

    tes3x_ini_xbox(key, "", buf, sizeof(buf));
    while (*s == ' ' || *s == '\t')
        s++;
    while (*s >= '0' && *s <= '9') {
        any = 1;
        v = v * 10 + (*s++ - '0');
    }
    return any ? v : dflt;
}

void tes3x_prof_init(void)
{
    u32 i;

    tes3x_prof_target[TES3X_PROF_CALIB] = (u32)(unsigned int)&prof_calib_target;
    KeQuerySystemTime(&prof_session);
    prof_tsc_init = prof_tsc();
    prof_reset_time = prof_session;
    prof_reset_tsc = prof_tsc_init;
    for (i = 0; i < TES3X_PROF_SLOTS; i++) {
        if (tes3x_prof_target[i])
            tes3x_log_hex("prof.target", tes3x_prof_target[i]);
    }
}

void tes3x_prof_frame(void)
{
    static const u32 bucket_ms[TES3X_PROF_BUCKETS - 1] = {8, 12, 16, 20, 25, 33, 50};
    u64 now = prof_tsc();

    if (!prof_ready) {
        u32 seconds;

        prof_dump_frames = (u32)prof_uint("ProfileDumpFrames", 0);
        seconds = (u32)prof_uint("ProfileDumpSeconds", TES3X_PROF_PREMENU_SECONDS);
        if (seconds && seconds < TES3X_PROF_SECONDS_MIN)
            seconds = TES3X_PROF_SECONDS_MIN;
        if (seconds > TES3X_PROF_SECONDS_MAX)
            seconds = TES3X_PROF_SECONDS_MAX;
        prof_dump_seconds = seconds;
        tes3x_log("prof.dump_seconds", seconds);
        prof_ready = 1;
    }
    if (frame_last) {
        u64 d = now - frame_last;
        u32 i;

        frame_count++;
        frame_total += d;
        if (!frame_lo || d < frame_lo)
            frame_lo = d;
        if (d > frame_hi)
            frame_hi = d;
        for (i = 0; i < TES3X_PROF_BUCKETS - 1; i++) {
            if (d < (u64)bucket_ms[i] * PROF_CYCLES_PER_MS)
                break;
        }
        frame_hist[i]++;
    }
    frame_last = now;

    if (prof_dump_frames && frame_count && frame_count % prof_dump_frames == 0)
        prof_dump(TES3X_PROF_DUMP_FRAME);
}

static int prof_text_equal(const char *a, const char *b)
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

int tes3x_prof_command(const char *text)
{
    if (prof_text_equal(text, "tes3xprof")) {
        prof_dump(TES3X_PROF_DUMP_CONSOLE);
        return 1;
    }
    if (prof_text_equal(text, "tes3xprof reset")) {
        prof_reset();
        tes3x_log("prof.reset", prof_dump_seq);
        return 1;
    }
    if (prof_text_equal(text, "tes3xprof mark")) {
        prof_dump(TES3X_PROF_DUMP_MARK);
        prof_reset();
        return 1;
    }
    return 0;
}
