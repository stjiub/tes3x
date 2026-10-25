/* Load topic-type dialogue as stubs and build a topic's responses when a reader first needs them.
 *
 * loadRecord places each plugin INFO by its INAM and PNAM across plugins. For a topic (DIAL type
 * 0) the same placement is replayed on stubs that keep only the winning record's plugin and
 * offset, and the constructed INFO is deleted, so the topic's chain stays empty through load.
 * getFilteredInfo, getInfoAtIndex (the save's XNAM) and journalAdd build the chain from the
 * plugins in stub order before reading it. Readers that only look for heard responses see none,
 * which is right: nothing is heard in a chain that was never built.
 *
 * [Xbox] LazyDialogue=verify keeps every INFO and records the stubs alongside for comparison.
 */

#include "tes3xini.h"
#include "tes3xlog.h"

#define DIAL_TYPE 0x14
#define DIAL_INFO 0x18
#define INFO_FLAGS 0x08
#define INFO_SOURCE 0x0C
#define INFO_LINK 0x10
#define INFO_TYPE 0x14
#define INFO_NEXT 0x1C
#define INFO_OFFSET 0x24
#define FLAG_MASTER 0x01
#define FLAG_LINKED 0x08
#define FLAG_DELETED 0x20
#define FILE_PATH 0x110
#define FILE_NAME 0x0C
#define FILE_FLAGS 0x4D8
#define FILE_INDEX 0x4E0
#define FILE_SIZE 0x574
#define RECORDS_ACTIVE_MODS 0xAE6C
#define RECORDS_SAVING 0xB381

#define SLOTS 16384u
#define CHUNK 0x10000u
#define STUB_PREVIOUS 1u /* PNAM is not empty */

typedef void *(__thiscall *fn_heap_allocate)(void *, u32, const char *, u32);
typedef void(__thiscall *fn_heap_free)(void *, void *);
typedef void(__cdecl *fn_tagged_free)(void *);
typedef void *(__cdecl *fn_info_new)(u32);
typedef void *(__thiscall *fn_info_ctor)(void *);
typedef void(__thiscall *fn_deleting_dtor)(void *, u32);
typedef u8(__stdcall *fn_load_from_file)(void *, void *);
typedef void *(__thiscall *fn_file_ctor)(void *, const char *, const char *, u32);
typedef void(__thiscall *fn_file_collect)(void *, u32, u32);
typedef u8(__thiscall *fn_file_reopen)(void *, u32, u32);
typedef u8(__thiscall *fn_file_seek)(void *, u32);
typedef void(__thiscall *fn_file_plain)(void *);

TES3X_OPTIONAL extern u32 tes3x_dialogue_vtable;
TES3X_OPTIONAL void __cdecl tes3x_info_names_free(void *ptr);
TES3X_OPTIONAL void __stdcall tes3x_info_table_free(void *ptr);

struct stub {
    struct stub *next;
    u8 *source;
    u32 offset;
    u32 flags;
    u64 id;
    u64 previous;
    u32 previous_exact;
};

struct topic {
    u8 *dial;
    struct stub *head;
    u32 built;
};

enum { MODE_UNREAD, MODE_OFF, MODE_ON, MODE_VERIFY };

static struct topic *slots[SLOTS];
static u8 *chunk;
static u32 chunk_left, mode, reported;
static u32 topics, stubs, unstubbable, chunk_kb, built_topics, built_infos, failed;

static void *heap_allocate(u32 size)
{
    return ((fn_heap_allocate)TES3X_LAZY_HEAP_ALLOCATE)((void *)TES3X_LAZY_HEAP_OBJECT, size,
                                                        "tes3xlazydialogue", 0);
}

static void heap_free(void *ptr)
{
    ((fn_heap_free)TES3X_LAZY_HEAP_FREE)((void *)TES3X_LAZY_HEAP_OBJECT, ptr);
}

static void *take(u32 size)
{
    void *at;

    size = (size + 7) & ~7u;
    if (chunk_left < size) {
        chunk = heap_allocate(CHUNK);
        if (!chunk)
            return 0;
        chunk_left = CHUNK;
        chunk_kb += CHUNK / 1024;
    }
    at = chunk;
    chunk += size;
    chunk_left -= size;
    return at;
}

static u32 slot_of(const u8 *dial)
{
    return ((u32)dial * 2654435761u) >> 18;
}

static struct topic *find_topic(const u8 *dial)
{
    u32 i = slot_of(dial);

    while (slots[i]) {
        if (slots[i]->dial == dial)
            return slots[i];
        i = (i + 1) & (SLOTS - 1);
    }
    return 0;
}

static struct topic *add_topic(u8 *dial)
{
    u32 i = slot_of(dial);
    struct topic *t;

    if (topics >= SLOTS * 3 / 4)
        return 0;
    while (slots[i])
        i = (i + 1) & (SLOTS - 1);
    t = take(sizeof(*t));
    if (!t)
        return 0;
    t->dial = dial;
    t->head = 0;
    t->built = 0;
    slots[i] = t;
    topics++;
    return t;
}

/* findInfo and appendInfo compare names with _stricmp; the override path's PNAM test is strcmp. */
static u64 fold_hash(const char *s)
{
    u64 h = 0xCBF29CE484222325ull;

    for (; s && *s; s++) {
        u8 c = (u8)*s;
        if (c >= 'A' && c <= 'Z')
            c += 'a' - 'A';
        h = (h ^ c) * 0x100000001B3ull;
    }
    return h;
}

static u32 exact_hash(const char *s)
{
    u32 h = 0x811C9DC5u;

    for (; s && *s; s++)
        h = (h ^ (u8)*s) * 0x01000193u;
    return h;
}

/* The engine's chain operations, replayed literally on stubs. */
static struct stub *find_id(struct topic *t, u64 id)
{
    struct stub *s;

    for (s = t->head; s; s = s->next)
        if (s->id == id)
            return s;
    return 0;
}

static void append(struct topic *t, struct stub *s, u32 at_tail)
{
    struct stub *p;

    if (!(s->flags & STUB_PREVIOUS)) {
        s->next = t->head;
        t->head = s;
        return;
    }
    if (!at_tail)
        for (p = t->head; p; p = p->next)
            if (p->id == s->previous) {
                s->next = p->next;
                p->next = s;
                return;
            }
    if (!t->head) {
        t->head = s;
        return;
    }
    for (p = t->head; p->next; p = p->next)
        ;
    p->next = s;
}

static void remove_stub(struct topic *t, struct stub *s)
{
    struct stub *before = 0, *p = t->head, *q;

    if (p) {
        do {
            q = p;
            if (s == q)
                break;
            p = q->next;
            before = q;
        } while (q->next);
        if (before) {
            before->next = s->next;
            s->next = 0;
            return;
        }
    }
    t->head = s->next;
    s->next = 0;
}

static int index_of(struct topic *t, struct stub *s)
{
    struct stub *p = t->head;
    int i = 0;

    if (!p)
        return -1;
    while (p != s) {
        p = p->next;
        i++;
        if (!p)
            return -1;
    }
    return i;
}

static void insert_at(struct topic *t, struct stub *s, int index)
{
    struct stub *p = t->head;

    if (!index || !p) {
        s->next = p;
        t->head = s;
        return;
    }
    do {
        if (!--index)
            break;
        p = p->next;
    } while (p);
    if (p) {
        s->next = p->next;
        p->next = s;
    }
}

static void read_mode(void)
{
    char value[16];

    tes3x_ini_xbox("LazyDialogue", "on", value, sizeof(value));
    if ((value[0] | 0x20) == 'v')
        mode = MODE_VERIFY;
    else if (value[0] == '0' || ((value[0] | 0x20) == 'o' && (value[1] | 0x20) == 'f'))
        mode = MODE_OFF;
    else
        mode = MODE_ON;
    tes3x_log("lazy.mode", mode);
}

static __attribute__((used, noinline)) u32 __stdcall place(u8 *info, u8 *file, u32 at_tail)
{
    u8 *dial = *(u8 **)TES3X_VALUE(LAZY_TOPIC);
    char **names = *(char ***)(info + INFO_LINK);
    struct topic *t;
    struct stub n, *s, *existing, *prev;

    (void)file;
    if (mode == MODE_UNREAD)
        read_mode();
    if (mode == MODE_OFF || !dial || dial[DIAL_TYPE] != 0 || !names)
        return 0;
    if (!*(u32 *)(info + INFO_OFFSET) || !*(u8 **)(info + INFO_SOURCE)) {
        unstubbable++;
        return 0;
    }
    t = find_topic(dial);
    if (!t)
        t = add_topic(dial);
    if (!t) {
        unstubbable++;
        return 0;
    }
    n.next = 0;
    n.source = *(u8 **)(info + INFO_SOURCE);
    n.offset = *(u32 *)(info + INFO_OFFSET);
    n.id = fold_hash(names[0]);
    n.previous = fold_hash(names[1]);
    n.previous_exact = exact_hash(names[1]);
    n.flags = (names[1] && names[1][0] ? STUB_PREVIOUS : 0) |
              (*(u32 *)(info + INFO_FLAGS) & FLAG_DELETED);

    existing = at_tail & 0xFF ? 0 : find_id(t, n.id);
    if (!existing) {
        s = take(sizeof(*s));
        if (!s) {
            unstubbable++;
            return 0;
        }
        *s = n;
        append(t, s, at_tail & 0xFF);
        stubs++;
    } else {
        if (existing->previous_exact != n.previous_exact) {
            prev = n.flags & STUB_PREVIOUS ? find_id(t, n.previous) : 0;
            if (!prev || prev->next != existing) {
                remove_stub(t, existing);
                insert_at(t, existing, prev ? index_of(t, prev) + 1 : 0);
            }
        }
        n.next = existing->next;
        n.flags |= existing->flags & FLAG_DELETED;
        *existing = n;
    }
    if (mode == MODE_VERIFY)
        return 0;
    (*(fn_deleting_dtor *)*(void ***)info)(info, 1);
    return 1;
}

/* Replaces loadRecord's mov ecx, [topic]; mov al, [esp+0x204] ... je after loadFromFile. */
__attribute__((naked)) void tes3x_lazy_dialogue_info_hook(void)
{
    __asm__ volatile(
        "pushal\n\t"
        "pushl 0x224(%esp)\n\t"
        "pushl 0x224(%esp)\n\t"
        "pushl %edi\n\t"
        "call _place@12\n\t"
        "movl %eax, 28(%esp)\n\t"
        "popal\n\t"
        "testl %eax, %eax\n\t"
        "jz 1f\n\t"
        "movl $1, %eax\n\t"
        "pushl $" TES3X_SYM(LAZY_INFO_DONE) "\n\t"
        "retl\n\t"
        "1:\n\t"
        "movl " TES3X_SYM(LAZY_TOPIC) ", %ecx\n\t"
        "movb 0x204(%esp), %al\n\t"
        "movb 0x14(%ecx), %dl\n\t"
        "movb %dl, 0x14(%edi)\n\t"
        "testb %al, %al\n\t"
        "jz 2f\n\t"
        "pushl $" TES3X_SYM(LAZY_INFO_TAIL) "\n\t"
        "retl\n\t"
        "2:\n\t"
        "pushl $" TES3X_SYM(LAZY_INFO_NOTAIL) "\n\t"
        "retl\n\t");
}

static u8 *records(void)
{
    u8 *handler = *(u8 **)TES3X_VALUE(LAZY_DATA_HANDLER);

    return handler ? *(u8 **)handler : 0;
}

/* DialogueInfo::getText's way of reopening a plugin: Morrowind.esm through one cached file,
 * anything else through a temporary one. */
static u8 *open_source(u8 *source, u32 *temporary)
{
    u8 **cached = (u8 **)TES3X_VALUE(LAZY_CACHED_FILE);
    u8 *file;
    u32 active = *(u32 *)(records() + RECORDS_ACTIVE_MODS);

    *temporary = 0;
    if ((source[FILE_FLAGS] & 1) && !*(u32 *)(source + FILE_INDEX)) {
        if (*cached)
            return *cached;
        file = heap_allocate(FILE_SIZE);
        if (!file)
            return 0;
        ((fn_file_ctor)TES3X_LAZY_FILE_CTOR)(file, (const char *)source + FILE_PATH,
                                             (const char *)source + FILE_NAME, 0);
        *cached = file;
        ((fn_file_collect)TES3X_LAZY_FILE_COLLECT)(file, active, 0);
        return ((fn_file_reopen)TES3X_LAZY_FILE_REOPEN)(file, 0, 0) ? file : 0;
    }
    file = heap_allocate(FILE_SIZE);
    if (!file)
        return 0;
    ((fn_file_ctor)TES3X_LAZY_FILE_CTOR)(file, (const char *)source + FILE_PATH,
                                         (const char *)source + FILE_NAME, 0);
    ((fn_file_collect)TES3X_LAZY_FILE_COLLECT)(file, active, 0);
    *temporary = 1;
    if (!((fn_file_reopen)TES3X_LAZY_FILE_REOPEN)(file, 0, 0)) {
        ((fn_file_plain)TES3X_LAZY_FILE_DTOR)(file);
        heap_free(file);
        return 0;
    }
    return file;
}

static void close_source(u8 *file, u32 temporary)
{
    if (file != *(u8 **)TES3X_VALUE(LAZY_CACHED_FILE))
        ((fn_file_plain)TES3X_LAZY_FILE_CLOSE)(file);
    if (temporary) {
        ((fn_file_plain)TES3X_LAZY_FILE_DTOR)(file);
        heap_free(file);
    }
}

static void free_link(u8 *info)
{
    void **table = *(void ***)(info + INFO_LINK);

    if (!table)
        return;
    if (tes3x_info_names_free)
        tes3x_info_names_free(table[0]);
    else
        ((fn_tagged_free)TES3X_LAZY_NAMES_FREE)(table[0]);
    table[0] = table[1] = table[2] = 0;
    if (tes3x_info_table_free)
        tes3x_info_table_free(table);
    else
        heap_free(table);
    *(void **)(info + INFO_LINK) = 0;
}

/* One INFO as loadRecord leaves it after the override copy and freeLoadNames. */
static u8 *load_one(u8 *file, struct stub *s, const u8 *dial)
{
    u8 *info = ((fn_info_new)TES3X_LAZY_INFO_NEW)(0x2C);
    u32 *flags;

    if (!info)
        return 0;
    ((fn_info_ctor)TES3X_LAZY_INFO_CTOR)(info);
    info[INFO_TYPE] = dial[DIAL_TYPE];
    if (!((fn_file_seek)TES3X_LAZY_FILE_SEEK)(file, s->offset)) {
        (*(fn_deleting_dtor *)*(void ***)info)(info, 1);
        return 0;
    }
    ((fn_load_from_file)TES3X_LAZY_LOAD_FROM_FILE)(info, file);
    info[INFO_TYPE] = dial[DIAL_TYPE];
    *(u8 **)(info + INFO_SOURCE) = s->source;
    *(u32 *)(info + INFO_OFFSET) = s->offset;
    flags = (u32 *)(info + INFO_FLAGS);
    *flags = (*flags & ~FLAG_MASTER) | (s->source[FILE_FLAGS] & 1) | (s->flags & FLAG_DELETED) |
             FLAG_LINKED;
    free_link(info);
    if (&tes3x_dialogue_vtable && tes3x_dialogue_vtable)
        *(const u8 **)(info + INFO_LINK) = dial;
    return info;
}

static void build(struct topic *t)
{
    u8 *data = records(), **infos, *file, *source, **link;
    struct stub *s;
    u32 count = 0, i, temporary, saving;

    t->built = 1;
    for (s = t->head; s; s = s->next)
        count++;
    if (!count || !data)
        return;
    infos = heap_allocate(count * 4);
    if (!infos) {
        failed += count;
        return;
    }
    for (i = 0; i < count; i++)
        infos[i] = 0;
    saving = data[RECORDS_SAVING];
    data[RECORDS_SAVING] = 0;
    for (;;) {
        for (s = t->head, i = 0; s && infos[i]; s = s->next, i++)
            ;
        if (!s)
            break;
        source = s->source;
        file = open_source(source, &temporary);
        for (; s; s = s->next, i++) {
            if (infos[i] || s->source != source)
                continue;
            infos[i] = file ? load_one(file, s, t->dial) : 0;
            if (!infos[i]) {
                infos[i] = (u8 *)1;
                failed++;
            }
        }
        if (file)
            close_source(file, temporary);
    }
    data[RECORDS_SAVING] = (u8)saving;
    for (link = (u8 **)(t->dial + DIAL_INFO); *link; link = (u8 **)(*link + INFO_NEXT))
        ;
    for (i = 0; i < count; i++) {
        if (infos[i] == (u8 *)1)
            continue;
        *link = infos[i];
        link = (u8 **)(infos[i] + INFO_NEXT);
        built_infos++;
    }
    *link = 0;
    heap_free(infos);
    built_topics++;
}

static void report(void)
{
    reported = 1;
    tes3x_log("lazy.topics", topics);
    tes3x_log("lazy.stubs", stubs);
    tes3x_log("lazy.stub_kb", chunk_kb);
    tes3x_log("lazy.unstubbable", unstubbable);
}

static __attribute__((used, noinline)) void __stdcall ensure(const u8 *dial)
{
    struct topic *t;

    if (!reported)
        report();
    if (mode != MODE_ON || !dial)
        return;
    t = find_topic(dial);
    if (t && !t->built)
        build(t);
}

/* Called first by each reader stub with the topic in ecx; keeps every register. */
__attribute__((naked)) void tes3x_lazy_dialogue_ensure(void)
{
    __asm__ volatile(
        "pushal\n\t"
        "pushl %ecx\n\t"
        "call _ensure@4\n\t"
        "popal\n\t"
        "retl\n\t");
}

/* getFilteredInfo: push esi; push edi; mov edi, ecx; mov esi, [edi+0x18] */
__attribute__((naked)) void tes3x_lazy_dialogue_filtered(void)
{
    __asm__ volatile(
        "call _tes3x_lazy_dialogue_ensure\n\t"
        "pushl %esi\n\t"
        "pushl %edi\n\t"
        "movl %ecx, %edi\n\t"
        "movl 0x18(%edi), %esi\n\t"
        "pushl $" TES3X_SYM(LAZY_FILTERED_RESUME) "\n\t"
        "retl\n\t");
}

/* getInfoAtIndex: mov eax, [ecx+0x18]; test eax, eax */
__attribute__((naked)) void tes3x_lazy_dialogue_at_index(void)
{
    __asm__ volatile(
        "call _tes3x_lazy_dialogue_ensure\n\t"
        "movl 0x18(%ecx), %eax\n\t"
        "pushl $" TES3X_SYM(LAZY_AT_INDEX_RESUME) "\n\t"
        "testl %eax, %eax\n\t"
        "retl\n\t");
}

/* journalAdd: push ebx; push ebp; mov ebp, [esp+0xc] */
__attribute__((naked)) void tes3x_lazy_dialogue_journal(void)
{
    __asm__ volatile(
        "call _tes3x_lazy_dialogue_ensure\n\t"
        "pushl %ebx\n\t"
        "pushl %ebp\n\t"
        "movl 0xc(%esp), %ebp\n\t"
        "pushl $" TES3X_SYM(LAZY_JOURNAL_RESUME) "\n\t"
        "retl\n\t");
}

/* Test probe: in verify mode, compare every stub list with the chain the engine built. */
u32 tes3x_lazy_dialogue_check(void)
{
    u32 i, checked = 0, mismatched = 0, shown = 0;

    for (i = 0; i < SLOTS; i++) {
        struct topic *t = slots[i];
        struct stub *s;
        u8 *info;
        u32 n = 0, bad = 0;

        if (!t)
            continue;
        for (s = t->head, info = *(u8 **)(t->dial + DIAL_INFO); s || info;
             n++, s = s ? s->next : 0, info = info ? *(u8 **)(info + INFO_NEXT) : 0) {
            if (!s || !info || s->source != *(u8 **)(info + INFO_SOURCE) ||
                s->offset != *(u32 *)(info + INFO_OFFSET) ||
                (s->flags & FLAG_DELETED) != (*(u32 *)(info + INFO_FLAGS) & FLAG_DELETED)) {
                bad = 1;
                break;
            }
        }
        checked++;
        if (bad) {
            mismatched++;
            if (shown++ < 8)
                tes3x_log_hex3("lazy.mismatch_at", (u32)t->dial, n, (u32)s);
        }
    }
    tes3x_log("lazy.checked", checked);
    tes3x_log("lazy.mismatch", mismatched);
    return mismatched;
}

/* Test probe: build every topic still a stub. */
u32 tes3x_lazy_dialogue_build_all(void)
{
    u32 i, before = built_topics;

    if (!reported)
        report();
    for (i = 0; i < SLOTS; i++)
        if (slots[i] && !slots[i]->built && mode == MODE_ON)
            build(slots[i]);
    tes3x_log("lazy.built_topics", built_topics - before);
    tes3x_log("lazy.built_infos", built_infos);
    tes3x_log("lazy.failed", failed);
    return built_topics - before;
}
