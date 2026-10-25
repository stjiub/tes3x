/* Grow the heap's size-class pools instead of spilling into the region. Memory_Heap::Allocate
 * serves blocks of 8-176 bytes from fixed pools sized for retail; once one is full, every further
 * block of that size takes an 8-byte header in the region. Here an exhausted pool gets a further
 * pool of the same size linked after it. Free and sizeOfAllocation find any pool through the
 * global pool list, which the engine's pool constructor joins.
 */

#include "tes3x_thunks.h"
#include "tes3xnt.h"
#include "tes3xlog.h"

#ifndef TES3X_POOL_CTOR
#error "define TES3X_POOL_CTOR to the VA of MemoryPool_Simple's constructor"
#endif

#define POOL_SIZE 0x90
#define POOL_CAPACITY 0x44
#define POOL_BUMP 0x4C
#define POOL_FREE_HEAD 0x5C
#define POOL_ELEMENT 0x64

#define MAX_POOLS 128
#define MAX_CLASSES 16
#define FIRST_CHUNK (64u * 1024u)
#define MAX_CHUNK (256u * 1024u)

typedef void *(__fastcall *fn_pool_ctor)(void *pool, void *unused, const char *name,
                                          int element, int capacity);

typedef struct {
    u8 *pool;
    u32 *next;
} pool_node;

typedef struct {
    u32 element;
    u32 chunk;
    u8 *newest;
} size_class;

static u32 pools_body[MAX_POOLS][POOL_SIZE / 4];
static pool_node nodes[MAX_POOLS];
static size_class classes[MAX_CLASSES];
static u32 pool_count;
static u32 grown_bytes;
static int growing;

static u32 field(const u8 *pool, u32 off)
{
    return *(const u32 *)(pool + off);
}

static int exhausted(const u8 *pool)
{
    return !field(pool, POOL_FREE_HEAD) &&
           (int)field(pool, POOL_CAPACITY) <= (int)field(pool, POOL_BUMP);
}

static size_class *class_for(u32 element)
{
    u32 i;

    for (i = 0; i < MAX_CLASSES; i++) {
        if (classes[i].element == element)
            return &classes[i];
        if (!classes[i].element) {
            classes[i].element = element;
            classes[i].chunk = FIRST_CHUNK;
            return &classes[i];
        }
    }
    return 0;
}

static void grow(pool_node *after, size_class *cls)
{
    u8 *pool;
    pool_node *node;

    if (pool_count >= MAX_POOLS)
        return;
    pool = (u8 *)pools_body[pool_count];
    node = &nodes[pool_count];
    /* The constructor allocates its global-list node from this heap, re-entering Allocate. */
    growing = 1;
    ((fn_pool_ctor)TES3X_POOL_CTOR)(pool, 0, "tes3x heap pool", (int)cls->element,
                                    (int)cls->chunk);
    growing = 0;
    pool_count++;
    if (!*(void **)(pool + 0x48))
        return;
    grown_bytes += field(pool, POOL_CAPACITY);
    node->pool = pool;
    node->next = after->next;
    after->next = (u32 *)node;
    cls->newest = pool;
    tes3x_log("pool.grow", cls->element);
    tes3x_log("pool.grown_kb", grown_bytes / 1024);
    if (cls->chunk < MAX_CHUNK)
        cls->chunk *= 2;
}

/* Allocate's pool loop asks this instead of isExhausted. A nonzero answer moves the loop to the
 * next node, which is where a new pool of the class is linked. */
int __stdcall tes3x_pool_exhausted(pool_node *node)
{
    size_class *cls;

    if (!exhausted(node->pool))
        return 0;
    if (growing)
        return 1;
    cls = class_for(field(node->pool, POOL_ELEMENT));
    if (!cls)
        return 1;
    /* Grown pools of a class follow one another, newest last. */
    if (cls->newest && cls->newest != node->pool)
        return 1;
    grow(node, cls);
    return 1;
}

/* Replaces Allocate's `call isExhausted` (pool in eax). Allocate's loop node is at [ebp-0x10]. */
__attribute__((naked)) void tes3x_pool_grow_hook(void)
{
    __asm__ volatile(
        "pushl %ecx\n\t"
        "pushl %edx\n\t"
        "pushl -0x10(%ebp)\n\t"
        "call _tes3x_pool_exhausted@4\n\t"
        "popl %edx\n\t"
        "popl %ecx\n\t"
        "ret\n\t");
}
