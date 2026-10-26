/* Order the topic list longest name first with one stable sort, not the engine's bubble sort.
 *
 * loadAllFiles sorts NonDynamicData's topics by name length, longest first, keeping the load order
 * of equal lengths. The engine makes count-1 full bubble passes with two strlens per pair: 29.8 s
 * on hardware for full TR's 7,070 topics. A stable merge sort of the same entries leaves the same
 * order in the same nodes, and the loading bar still advances by count-1 records.
 */

#include "tes3x_thunks.h"
#include "tes3xnt.h"
#include "tes3xlog.h"

#ifndef TES3X_TOPICSORT_HANDLER
#error "define TES3X_TOPICSORT_HANDLER to the DataHandler pointer"
#endif
#ifndef TES3X_TOPICSORT_LIST
#error "define TES3X_TOPICSORT_LIST to NonDynamicData's topic list offset"
#endif
#ifndef TES3X_TOPICSORT_INCREMENT
#error "define TES3X_TOPICSORT_INCREMENT to DataHandler::incrementLoadedRecords"
#endif
#ifndef TES3X_TOPICSORT_FRACTION
#error "define TES3X_TOPICSORT_FRACTION to DataHandler::getTotalLoadedRecordsFraction"
#endif
#ifndef TES3X_TOPICSORT_UPDATE
#error "define TES3X_TOPICSORT_UPDATE to UI::updateLoadingMenu"
#endif

#define MmAllocateSystemMemory KFN(THUNK_MmAllocateSystemMemory, fn_MmAllocateSystemMemory)
#define MmFreeSystemMemory KFN(THUNK_MmFreeSystemMemory, fn_MmFreeSystemMemory)

#define LIST_COUNT 4u
#define LIST_HEAD 8u
#define NODE_NEXT 4u
#define NODE_DATA 8u
#define TOPIC_NAME 0x10u

typedef void(__attribute__((thiscall)) *fn_increment)(void *handler, int records);
typedef float(__attribute__((thiscall)) *fn_fraction)(void *handler);
typedef void(__cdecl *fn_update)(float percent);

#define AT(p, off) (*(u32 *)((u32)(p) + (off)))

typedef struct {
    u32 len, topic, order;
} entry;

static u32 name_length(u32 topic)
{
    const char *s = *(const char **)(topic + TOPIC_NAME), *p;

    if (!s)
        return 0;
    for (p = s; *p; p++)
        ;
    return (u32)(p - s);
}

/* Bottom-up; on equal lengths the left run's entry goes first, so the sort is stable. */
static entry *merge_sort(entry *a, entry *b, u32 n)
{
    u32 width, i, l, r, lend, rend, k;
    entry *t;

    for (width = 1; width < n; width *= 2) {
        for (i = 0; i < n; i += 2 * width) {
            l = k = i;
            lend = r = n - i > width ? i + width : n;
            rend = n - i > 2 * width ? i + 2 * width : n;
            while (l < lend && r < rend)
                b[k++] = a[r].len > a[l].len ? a[r++] : a[l++];
            while (l < lend)
                b[k++] = a[l++];
            while (r < rend)
                b[k++] = a[r++];
        }
        t = a;
        a = b;
        b = t;
    }
    return a;
}

/* The engine's own passes, for a list this sort does not take. */
static void bubble(u32 list, u32 passes)
{
    u32 node, next, data;

    while (passes--)
        for (node = AT(list, LIST_HEAD); node; node = AT(node, NODE_NEXT)) {
            next = AT(node, NODE_NEXT);
            if (next && AT(node, NODE_DATA) && AT(next, NODE_DATA) &&
                name_length(AT(next, NODE_DATA)) > name_length(AT(node, NODE_DATA))) {
                data = AT(node, NODE_DATA);
                AT(node, NODE_DATA) = AT(next, NODE_DATA);
                AT(next, NODE_DATA) = data;
            }
        }
}

static int sort(u32 list, u32 count)
{
    u32 node, n = 0, i, bytes, unsorted = 0;
    entry *a, *sorted;

    for (node = AT(list, LIST_HEAD); node; node = AT(node, NODE_NEXT)) {
        if (!AT(node, NODE_DATA))
            return 0;
        n++;
    }
    if (n != count)
        return 0;
    bytes = n * 2 * sizeof(entry);
    a = (entry *)MmAllocateSystemMemory(bytes, PAGE_READWRITE);
    if (!a)
        return 0;
    for (node = AT(list, LIST_HEAD), i = 0; node; node = AT(node, NODE_NEXT), i++) {
        a[i].topic = AT(node, NODE_DATA);
        a[i].len = name_length(a[i].topic);
        a[i].order = i;
    }
    sorted = merge_sort(a, a + n, n);
    for (node = AT(list, LIST_HEAD), i = 0; node; node = AT(node, NODE_NEXT), i++) {
        AT(node, NODE_DATA) = sorted[i].topic;
        if (i && (sorted[i].len > sorted[i - 1].len ||
                  (sorted[i].len == sorted[i - 1].len && sorted[i].order < sorted[i - 1].order)))
            unsorted++;
    }
    MmFreeSystemMemory(a, bytes);
    tes3x_log("topicsort.topics", n);
    if (unsorted)
        tes3x_log("topicsort.unsorted", unsorted);
    return 1;
}

/* Entered by jmp in place of Dialogue::sortTopicsByNameLength: __cdecl, no arguments. */
void __cdecl tes3x_topic_sort(void)
{
    void *handler = *(void **)TES3X_TOPICSORT_HANDLER;
    u32 list = AT(AT(handler, 0), TES3X_TOPICSORT_LIST);
    u32 count = AT(list, LIST_COUNT);

    if ((int)count <= 1)
        return;
    if (!sort(list, count)) {
        tes3x_log("topicsort.fallback", count);
        bubble(list, count - 1);
    }
    ((fn_increment)TES3X_TOPICSORT_INCREMENT)(handler, (int)(count - 1));
    ((fn_update)TES3X_TOPICSORT_UPDATE)(((fn_fraction)TES3X_TOPICSORT_FRACTION)(handler) * 100.0f);
}
