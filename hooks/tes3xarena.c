/* Size the video-memory arena from physical memory. Every texture and vertex buffer comes from one
 * contiguous arena of a hardcoded 0xF80000 bytes; once it is full, each further block becomes its
 * own page-rounded physical allocation from general memory. [Xbox] VideoMemoryKB may shrink the
 * arena on any console; only with more than 64 MB may it grow it. Overflow blocks are packed into
 * contiguous chunks of up to 1 MB instead of taking whole pages each.
 */

#include "tes3x_thunks.h"
#include "tes3xnt.h"
#include "tes3xlog.h"
#include "tes3xini.h"

#ifndef TES3X_INI_GET_STRING
#error "define TES3X_INI_GET_STRING to the VA of the ini string reader"
#endif
#ifndef TES3X_INI_PATH
#error "define TES3X_INI_PATH to the VA of the engine's ini filename string"
#endif
#ifndef TES3X_ARENA_PHYS_ALLOC
#error "define TES3X_ARENA_PHYS_ALLOC to XPhysicalAlloc"
#endif
#ifndef TES3X_ARENA_PHYS_FREE
#error "define TES3X_ARENA_PHYS_FREE to XPhysicalFree"
#endif

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

#define RETAIL_BYTES 0xF80000u
#define DEFAULT_KB (22u * 1024u)
#define MIN_KB 1024u
#define MAX_KB (48u * 1024u)
#define PAGES_64MB (64u * 1024u * 1024u / 4096u)

u32 tes3x_arena_size(void)
{
    MM_STATISTICS st;
    char buf[16];
    u32 kb = 0;
    int i, set = 0, small;

    tes3x_ini_xbox("VideoMemoryKB", "", buf, sizeof(buf));
    for (i = 0; buf[i] >= '0' && buf[i] <= '9' && kb < MAX_KB; i++, set = 1)
        kb = kb * 10 + (u32)(buf[i] - '0');
    st.Length = sizeof(st);
    small = MmQueryStatistics(&st) != 0 || st.TotalPhysicalPages <= PAGES_64MB;
    if (!set)
        kb = small ? RETAIL_BYTES / 1024 : DEFAULT_KB;
    if (small && kb * 1024 > RETAIL_BYTES)
        kb = RETAIL_BYTES / 1024;
    if (kb < MIN_KB)
        kb = MIN_KB;
    if (kb > MAX_KB)
        kb = MAX_KB;
    tes3x_log(set ? "arena.kb_ini" : "arena.kb", kb);
    return kb * 1024;
}

typedef void *(__stdcall *fn_phys_alloc)(u32 size, u32 max_address, u32 alignment, u32 protect);
typedef void(__stdcall *fn_phys_free)(void *);

#define CHUNK_BYTES 0x100000u
#define GRANULE 64u
#define GRANULES (CHUNK_BYTES / GRANULE)
#define MAX_CHUNKS 48
/* Larger blocks lose little to page rounding and would fragment the chunks. */
#define DIRECT_BYTES (CHUNK_BYTES / 4)

struct chunk {
    u8 *base;
    u32 granules, used;
    u32 bits[GRANULES / 32];
};

/* Only reached under the arena's own lock (+0x34), held around both calls. */
static struct chunk chunks[MAX_CHUNKS];
static u32 live_chunks, peak_chunks, chunk_failed;

/* Contiguous memory fragments on 64 MB, so smaller chunks are tried before whole pages. */
static const u32 chunk_sizes[] = {CHUNK_BYTES, CHUNK_BYTES / 4, CHUNK_BYTES / 16};

static int find_run(const struct chunk *c, u32 n)
{
    u32 i = 0, run = 0, start = 0, w;

    while (i < c->granules) {
        w = c->bits[i >> 5];
        if (!(i & 31) && w == 0xFFFFFFFFu) {
            run = 0;
            i += 32;
            continue;
        }
        if (!(i & 31) && !w) {
            if (!run)
                start = i;
            run += 32;
            i += 32;
        } else if (w & (1u << (i & 31))) {
            run = 0;
            i++;
            continue;
        } else {
            if (!run)
                start = i;
            run++;
            i++;
        }
        if (run >= n)
            return (int)start;
    }
    return -1;
}

static void mark(struct chunk *c, u32 at, u32 n, int set)
{
    u32 i;

    for (i = at; i < at + n; i++) {
        if (set)
            c->bits[i >> 5] |= 1u << (i & 31);
        else
            c->bits[i >> 5] &= ~(1u << (i & 31));
    }
    c->used = set ? c->used + n : c->used - n;
}

static void *take(u32 n)
{
    u32 i;
    int at;

    for (i = 0; i < MAX_CHUNKS; i++) {
        if (!chunks[i].base || chunks[i].granules - chunks[i].used < n)
            continue;
        at = find_run(&chunks[i], n);
        if (at >= 0) {
            mark(&chunks[i], (u32)at, n, 1);
            return chunks[i].base + (u32)at * GRANULE;
        }
    }
    return 0;
}

void *__stdcall tes3x_arena_overflow_alloc(u32 size, u32 max_address, u32 alignment, u32 protect)
{
    u32 n = (size + GRANULE - 1) / GRANULE, i, k;
    struct chunk *c;
    void *p;

    if (size > DIRECT_BYTES || alignment)
        return ((fn_phys_alloc)TES3X_ARENA_PHYS_ALLOC)(size, max_address, alignment, protect);
    if ((p = take(n)) != 0)
        return p;
    for (i = 0; i < MAX_CHUNKS && chunks[i].base; i++)
        ;
    c = i < MAX_CHUNKS ? &chunks[i] : 0;
    for (k = 0; c && k < sizeof(chunk_sizes) / sizeof(chunk_sizes[0]); k++) {
        if (chunk_sizes[k] < n * GRANULE)
            break;
        c->base = ((fn_phys_alloc)TES3X_ARENA_PHYS_ALLOC)(chunk_sizes[k], max_address, 0, protect);
        if (c->base) {
            c->granules = chunk_sizes[k] / GRANULE;
            break;
        }
    }
    if (!c || !c->base) {
        if (!chunk_failed++)
            tes3x_log("arena.chunk_failed", live_chunks);
        return ((fn_phys_alloc)TES3X_ARENA_PHYS_ALLOC)(size, max_address, alignment, protect);
    }
    c->used = 0;
    for (i = 0; i < c->granules / 32; i++)
        c->bits[i] = 0;
    mark(c, 0, n, 1);
    if (++live_chunks > peak_chunks) {
        peak_chunks = live_chunks;
        tes3x_log("arena.chunks_peak", peak_chunks);
    }
    return c->base;
}

/* The block's header still holds its negated arena size, which excludes the 16-byte header. */
void __stdcall tes3x_arena_overflow_free(void *block)
{
    u8 *p = block;
    u32 i, n;

    for (i = 0; i < MAX_CHUNKS; i++) {
        if (!chunks[i].base || p < chunks[i].base || p >= chunks[i].base + chunks[i].granules * GRANULE)
            continue;
        n = ((u32)(-*(int *)p) + 0x10 + GRANULE - 1) / GRANULE;
        mark(&chunks[i], (u32)(p - chunks[i].base) / GRANULE, n, 0);
        if (!chunks[i].used) {
            ((fn_phys_free)TES3X_ARENA_PHYS_FREE)(chunks[i].base);
            chunks[i].base = 0;
            live_chunks--;
        }
        return;
    }
    ((fn_phys_free)TES3X_ARENA_PHYS_FREE)(block);
}

/* Replaces `mov ebx, 0xF80000` just before the arena constructor: eax holds the new heap object
 * and must survive; the size goes in ebx. */
__attribute__((naked)) void tes3x_arena_size_hook(void)
{
    __asm__ volatile(
        "pushl %eax\n\t"
        "pushl %ecx\n\t"
        "pushl %edx\n\t"
        "call _tes3x_arena_size\n\t"
        "movl %eax, %ebx\n\t"
        "popl %edx\n\t"
        "popl %ecx\n\t"
        "popl %eax\n\t"
        "ret\n\t");
}
