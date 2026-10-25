/* Load dialogue as stubs and build a topic's responses when a reader first needs them.
 *
 * loadRecord places each plugin INFO by its INAM and PNAM across plugins. For every DIAL type
 * the same placement is replayed on stubs that keep only the winning record's plugin and
 * offset, and the constructed INFO is deleted, so the topic's chain stays empty through load.
 * getFilteredInfo, getInfoAtIndex (the save's XNAM) and journalAdd build the chain from the
 * plugins in stub order before reading it. Readers that only look for heard responses see none,
 * which is right: nothing is heard in a chain that was never built. After the last plugin the
 * stubs shrink to 18-byte entries in chain order, without the names placement needed.
 *
 * Each stub also keeps the filters the INFO filter (0x000FFEA0) tests before or regardless of its
 * conditions: speaker, race, class, NPC faction, cell-name prefix and sex. getFilteredInfo builds
 * a topic only when one of its stubs passes them for the speaker; otherwise no response could be
 * chosen, and the empty chain returns none.
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
#define HANDLER_CELL 0xB540
#define INFO_SEX 0x19
#define INFO_CONDITIONS 0x20
#define OBJECT_TYPE 0x04
#define REF_BASE 0x28
#define TYPE_REFR 0x52464552u
#define TYPE_CREA 0x41455243u
#define TYPE_CONT 0x544E4F43u
#define TYPE_NPC 0x5F43504Eu
#define ACTOR_BASE 0x154
#define ACTOR_RACE 0x60
#define ACTOR_CLASS 0x64
#define ACTOR_FACTION 0x68
#define ACTOR_SEX 0x6C
#define OBJECT_ID 0x20

#define SLOTS 16384u
#define OBJECTS 32768u
#define CHUNK 0x10000u
#define STUB_PREVIOUS 0x01u   /* PNAM is not empty */
#define STUB_UNFILTERED 0x02u /* the filters did not fit the object table: always build */
#define FILTERS 5             /* speaker, race, class, NPC faction, cell: the INFO's filter tags */
#define FILTER_CELL 4
#define FACTION_NONE 0xFFFFu  /* faction filter -1: the speaker must have no faction */

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
typedef u32(__thiscall *fn_object_get)(const void *);

TES3X_OPTIONAL extern u32 tes3x_dialogue_vtable;
TES3X_OPTIONAL void __cdecl tes3x_info_names_free(void *ptr);
TES3X_OPTIONAL void __stdcall tes3x_info_table_free(void *ptr);

/* What a topic keeps after load, in chain order. */
struct entry {
    u32 offset;
    u8 source; /* index into sources */
    u8 flags;
    signed char sex;
    u8 unused;
    unsigned short filter[FILTERS]; /* slot + 1 in objects, 0 for none */
} __attribute__((packed));

/* The placement replay also needs the INFO's names, as hashes. Its PNAM's case-folded hash is
 * read only while the INFO itself is placed, so it is not kept. */
struct stub {
    struct stub *next;
    u32 previous_exact;
    u64 id;
    struct entry e;
};

struct topic {
    u8 *dial;
    union {
        struct stub *head;     /* until compact */
        struct entry *entries; /* after */
    };
    u32 count;
    u32 built;
};

enum { MODE_UNREAD, MODE_OFF, MODE_ON, MODE_VERIFY };

static struct topic *slots[SLOTS];
static u8 *sources[256];
static u32 objects[OBJECTS];
/* Stubs fill whole chunks in order, so stub i is at chunks[i / PER_CHUNK]. */
#define PER_CHUNK (CHUNK / sizeof(struct stub))
#define CHUNKS 256u
typedef char stub_size_check[sizeof(struct stub) == 40 ? 1 : -1];

static u8 *chunks[CHUNKS];
static u32 chunk_count, mode, reported, source_count, object_count, compacted;
static u32 topics, stubs, unstubbable, chunk_kb, built_topics, built_infos, failed;
static u32 skipped, unfiltered, late;

static void *heap_allocate(u32 size)
{
    return ((fn_heap_allocate)TES3X_LAZY_HEAP_ALLOCATE)((void *)TES3X_LAZY_HEAP_OBJECT, size,
                                                        "tes3xlazydialogue", 0);
}

static void heap_free(void *ptr)
{
    ((fn_heap_free)TES3X_LAZY_HEAP_FREE)((void *)TES3X_LAZY_HEAP_OBJECT, ptr);
}

static struct stub *stub_at(u32 i)
{
    return (struct stub *)chunks[i / PER_CHUNK] + i % PER_CHUNK;
}

static struct stub *take_stub(void)
{
    if (stubs == chunk_count * PER_CHUNK) {
        if (chunk_count == CHUNKS || !(chunks[chunk_count] = heap_allocate(CHUNK)))
            return 0;
        chunk_count++;
        chunk_kb += CHUNK / 1024;
    }
    return stub_at(stubs);
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
    /* Not from the chunks, which compact frees. */
    t = heap_allocate(sizeof(*t));
    if (!t)
        return 0;
    t->dial = dial;
    t->head = 0;
    t->count = 0;
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

static void append(struct topic *t, struct stub *s, u64 previous, u32 at_tail)
{
    struct stub *p;

    if (!(s->e.flags & STUB_PREVIOUS)) {
        s->next = t->head;
        t->head = s;
        return;
    }
    if (!at_tail)
        for (p = t->head; p; p = p->next)
            if (p->id == previous) {
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

static int source_index(u8 *source, u8 *index)
{
    u32 i;

    for (i = 0; i < source_count; i++)
        if (sources[i] == source)
            break;
    if (i == source_count) {
        if (source_count == 256)
            return 0;
        sources[source_count++] = source;
    }
    *index = (u8)i;
    return 1;
}

/* A filter's object as its slot + 1 in a table that never rehashes; 0 when the table is full. */
static unsigned short object_index(u32 object)
{
    u32 i = (object * 2654435761u) >> 17;

    while (objects[i]) {
        if (objects[i] == object)
            return (unsigned short)(i + 1);
        i = (i + 1) & (OBJECTS - 1);
    }
    if (object_count >= OBJECTS * 3 / 4)
        return 0;
    objects[i] = object;
    object_count++;
    return (unsigned short)(i + 1);
}

/* The INFO's filter nodes (+0x20: tag, next, value), as DialogueInfo's decoder 0x000FD5B0 reads
 * them for the filter. */
static void record_filters(struct entry *s, const u8 *info)
{
    const u8 *node;
    u32 i;

    for (i = 0; i < FILTERS; i++)
        s->filter[i] = 0;
    s->sex = (signed char)info[INFO_SEX];
    s->unused = 0;
    for (node = *(const u8 *const *)(info + INFO_CONDITIONS); node;
         node = *(const u8 *const *)(node + 4)) {
        u32 tag = *(const u32 *)node, value = *(const u32 *)(node + 8);

        if (tag >= FILTERS || !value || s->filter[tag])
            continue;
        if (tag == 3 && value == 0xFFFFFFFFu) {
            s->filter[tag] = FACTION_NONE;
            continue;
        }
        s->filter[tag] = object_index(value);
        if (!s->filter[tag]) {
            s->flags |= STUB_UNFILTERED;
            unfiltered++;
        }
    }
}

static __attribute__((used, noinline)) u32 __stdcall place(u8 *info, u8 *file, u32 at_tail)
{
    u8 *dial = *(u8 **)TES3X_VALUE(LAZY_TOPIC);
    char **names = *(char ***)(info + INFO_LINK);
    struct topic *t;
    struct stub n, *s, *existing, *prev;
    u64 previous;

    (void)file;
    if (mode == MODE_UNREAD)
        read_mode();
    if (mode == MODE_OFF || !dial || !names)
        return 0;
    if (compacted) {
        late++;
        return 0;
    }
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
    if (!source_index(*(u8 **)(info + INFO_SOURCE), &n.e.source)) {
        unstubbable++;
        return 0;
    }
    n.e.offset = *(u32 *)(info + INFO_OFFSET);
    n.id = fold_hash(names[0]);
    previous = fold_hash(names[1]);
    n.previous_exact = exact_hash(names[1]);
    n.e.flags = (names[1] && names[1][0] ? STUB_PREVIOUS : 0) |
                (*(u32 *)(info + INFO_FLAGS) & FLAG_DELETED);
    record_filters(&n.e, info);

    existing = at_tail & 0xFF ? 0 : find_id(t, n.id);
    if (!existing) {
        s = take_stub();
        if (!s) {
            unstubbable++;
            return 0;
        }
        *s = n;
        append(t, s, previous, at_tail & 0xFF);
        t->count++;
        stubs++;
    } else {
        if (existing->previous_exact != n.previous_exact) {
            prev = n.e.flags & STUB_PREVIOUS ? find_id(t, previous) : 0;
            if (!prev || prev->next != existing) {
                remove_stub(t, existing);
                insert_at(t, existing, prev ? index_of(t, prev) + 1 : 0);
            }
        }
        n.next = existing->next;
        n.e.flags |= existing->e.flags & FLAG_DELETED;
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
static u8 *load_one(u8 *file, const struct entry *s, const u8 *dial)
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
    *(u8 **)(info + INFO_SOURCE) = sources[s->source];
    *(u32 *)(info + INFO_OFFSET) = s->offset;
    flags = (u32 *)(info + INFO_FLAGS);
    *flags = (*flags & ~FLAG_MASTER) | (sources[s->source][FILE_FLAGS] & 1) |
             (s->flags & FLAG_DELETED) | FLAG_LINKED;
    free_link(info);
    if (&tes3x_dialogue_vtable && tes3x_dialogue_vtable)
        *(const u8 **)(info + INFO_LINK) = dial;
    return info;
}

static void build(struct topic *t)
{
    u8 *data = records(), **infos, *file, **link;
    const struct entry *e = t->entries;
    u32 count = t->count, i, j, temporary, saving, source;

    t->built = 1;
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
    for (i = 0;; i++) {
        while (i < count && infos[i])
            i++;
        if (i == count)
            break;
        source = e[i].source;
        file = open_source(sources[source], &temporary);
        for (j = i; j < count; j++) {
            if (infos[j] || e[j].source != source)
                continue;
            infos[j] = file ? load_one(file, &e[j], t->dial) : 0;
            if (!infos[j]) {
                infos[j] = (u8 *)1;
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
    tes3x_log("lazy.unfiltered", unfiltered);
    tes3x_log("lazy.filter_objects", object_count);
    tes3x_log("lazy.late", late);
}

/* By field: a packed struct assignment becomes a memcpy call, which the payload cannot link. */
static void copy_entry(struct entry *to, const struct entry *from)
{
    u32 i;

    to->offset = from->offset;
    to->source = from->source;
    to->flags = from->flags;
    to->sex = from->sex;
    to->unused = 0;
    for (i = 0; i < FILTERS; i++)
        to->filter[i] = from->filter[i];
}

static void swap_stubs(struct stub *a, struct stub *b)
{
    u32 *x = (u32 *)a, *y = (u32 *)b, i, w;

    for (i = 0; i < sizeof(struct stub) / 4; i++) {
        w = x[i];
        x[i] = y[i];
        y[i] = w;
    }
}

#define GROUP 0x8000u
#define UNPLACED 0xFFFFFFFFu

/* After the last plugin, each chain becomes an array of entries. The stubs are first sorted in
 * place into chain order, so each chunk is freed once read and the arrays can reuse its space. A
 * topic whose entries cannot be allocated keeps no responses, as when build cannot allocate. */
static __attribute__((used, noinline)) void compact(void)
{
    struct entry *group = 0;
    struct stub *s;
    u32 i, j, n = 0, read = 0, freed = 0, group_left = 0, group_kb = 0, lost = 0, bytes, size;

    if (compacted)
        return;
    compacted = 1;
    /* A stub's place in chain order, in its no longer needed previous_exact. insert_at can drop a
     * stub from its chain, as the engine drops the INFO; those go last. */
    for (i = 0; i < stubs; i++)
        stub_at(i)->previous_exact = UNPLACED;
    for (i = 0; i < SLOTS; i++)
        if (slots[i])
            for (s = slots[i]->head; s; s = s->next)
                s->previous_exact = n++;
    for (i = 0; i < stubs; i++)
        if (stub_at(i)->previous_exact == UNPLACED)
            stub_at(i)->previous_exact = n++;
    for (i = 0; i < stubs; i++)
        while ((j = stub_at(i)->previous_exact) != i)
            swap_stubs(stub_at(i), stub_at(j));

    for (i = 0; i < SLOTS; i++) {
        struct topic *t = slots[i];

        if (!t)
            continue;
        bytes = t->count * sizeof(struct entry);
        if (bytes > group_left) {
            size = bytes > GROUP ? bytes : GROUP;
            group = heap_allocate(size);
            group_left = group ? size : 0;
            group_kb += group ? size / 1024 : 0;
        }
        if (!group) {
            lost += t->count;
            t->entries = 0;
            t->built = 1;
        } else {
            for (j = 0; j < t->count; j++)
                copy_entry(&group[j], &stub_at(read + j)->e);
            t->entries = group;
            group += t->count;
            group_left -= bytes;
        }
        read += t->count;
        if (!group)
            t->count = 0;
        for (; freed < chunk_count && (freed + 1) * PER_CHUNK <= read; freed++) {
            heap_free(chunks[freed]);
            chunks[freed] = 0;
        }
    }
    for (; freed < chunk_count; freed++) {
        heap_free(chunks[freed]);
        chunks[freed] = 0;
    }
    failed += lost;
    tes3x_log("lazy.entry_kb", group_kb);
    tes3x_log("lazy.dropped", stubs - read);
    tes3x_log("lazy.compact_lost", lost);
}

/* Replaces loadAllFiles' call for the first topic after sortTopicsByNameLength. */
__attribute__((naked)) void tes3x_lazy_dialogue_loaded(void)
{
    __asm__ volatile(
        "pushal\n\t"
        "call _compact\n\t"
        "popal\n\t"
        "pushl $" TES3X_SYM(LAZY_LOADED_FIRST) "\n\t"
        "retl\n\t");
}

static __attribute__((used, noinline)) void __stdcall ensure(const u8 *dial)
{
    struct topic *t;

    if (!reported)
        report();
    if (mode != MODE_ON || !dial)
        return;
    compact();
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

static u32 vcall(const u8 *object, u32 slot)
{
    return (*(fn_object_get *)(*(const u8 *const *)object + slot))(object);
}

static const char *object_id(u32 object)
{
    return (const char *)vcall((const u8 *)object, OBJECT_ID);
}

static u32 filter_object(const struct entry *s, u32 tag)
{
    u32 i = s->filter[tag];

    return i && i != FACTION_NONE ? objects[i - 1] : 0;
}

/* The INFO filter's tests that need only the INFO and the speaker, in its order. The speaker
 * passed to getFilteredInfo becomes its base object for a reference, as in the filter. */
static int passes(const struct entry *s, const u8 *actor)
{
    u32 speaker = filter_object(s, 0), type, value, i;
    const u8 *cell;
    const char *name, *have;
    char want[64];

    if (s->flags & STUB_UNFILTERED)
        return 1;
    if (s->flags & FLAG_DELETED)
        return 0;
    if (*(const u32 *)(actor + OBJECT_TYPE) == TYPE_REFR)
        actor = *(const u8 *const *)(actor + REF_BASE);
    type = *(const u32 *)(actor + OBJECT_TYPE);
    if (speaker) {
        if (type == TYPE_CREA || type == TYPE_CONT || type == TYPE_NPC) {
            if (vcall(actor, ACTOR_BASE) != speaker)
                return 0;
        } else if ((u32)actor != speaker) {
            return 0;
        }
    }
    if (s->filter[FILTER_CELL]) {
        cell = *(const u8 *const *)(*(const u8 *const *)TES3X_VALUE(LAZY_DATA_HANDLER) +
                                    HANDLER_CELL);
        if (!cell)
            return 0;
        name = object_id(filter_object(s, FILTER_CELL));
        for (i = 0; name && name[i] && i < sizeof(want) - 1; i++)
            want[i] = name[i];
        want[i] = 0;
        have = object_id((u32)cell);
        if (!have)
            return 0;
        for (i = 0; want[i]; i++) {
            u8 a = (u8)want[i], b = (u8)have[i];
            if (a >= 'A' && a <= 'Z')
                a += 'a' - 'A';
            if (b >= 'A' && b <= 'Z')
                b += 'a' - 'A';
            if (a != b)
                return 0;
        }
    }
    if (type == TYPE_CREA)
        return vcall(actor, ACTOR_BASE) == speaker;
    if (type != TYPE_NPC)
        return 1;
    if ((value = filter_object(s, 1)) && vcall(actor, ACTOR_RACE) != value)
        return 0;
    if ((value = filter_object(s, 2)) && vcall(actor, ACTOR_CLASS) != value)
        return 0;
    if (s->filter[3] == FACTION_NONE ? vcall(actor, ACTOR_FACTION) != 0
                                     : (value = filter_object(s, 3)) &&
                                           vcall(actor, ACTOR_FACTION) != value)
        return 0;
    if (s->sex != -1 && (int)vcall(actor, ACTOR_SEX) != s->sex)
        return 0;
    return 1;
}

static int first_passing(const struct topic *t, const u8 *actor)
{
    u32 i;

    for (i = 0; i < t->count; i++)
        if (passes(&t->entries[i], actor))
            return (int)i;
    return -1;
}

static const u8 *last_actor;
static u32 last_reference, last_flag;

static __attribute__((used, noinline)) void __stdcall
ensure_for(const u8 *dial, const u8 *actor, u32 reference, u32 flag)
{
    struct topic *t;

    if (!reported)
        report();
    if (actor) {
        last_actor = actor;
        last_reference = reference;
        last_flag = flag;
    }
    if (mode != MODE_ON || !dial)
        return;
    compact();
    t = find_topic(dial);
    if (!t || t->built)
        return;
    if (!actor || first_passing(t, actor) >= 0)
        build(t);
    else
        skipped++;
}

/* getFilteredInfo: push esi; push edi; mov edi, ecx; mov esi, [edi+0x18] */
__attribute__((naked)) void tes3x_lazy_dialogue_filtered(void)
{
    __asm__ volatile(
        "pushal\n\t"
        "pushl 0x2c(%esp)\n\t"
        "pushl 0x2c(%esp)\n\t"
        "pushl 0x2c(%esp)\n\t"
        "pushl %ecx\n\t"
        "call _ensure_for@16\n\t"
        "popal\n\t"
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

    compact();
    for (i = 0; i < SLOTS; i++) {
        struct topic *t = slots[i];
        const struct entry *s = 0;
        u8 *info;
        u32 n = 0, bad = 0;

        if (!t)
            continue;
        for (info = *(u8 **)(t->dial + DIAL_INFO); n < t->count || info;
             n++, info = info ? *(u8 **)(info + INFO_NEXT) : 0) {
            s = n < t->count ? &t->entries[n] : 0;
            if (!s || !info || sources[s->source] != *(u8 **)(info + INFO_SOURCE) ||
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
    tes3x_log("lazy.built_before", built_topics);
    tes3x_log("lazy.skipped", skipped);
    for (i = 0; i < SLOTS; i++)
        if (slots[i] && !slots[i]->built && mode == MODE_ON)
            build(slots[i]);
    tes3x_log("lazy.built_topics", built_topics - before);
    tes3x_log("lazy.built_infos", built_infos);
    tes3x_log("lazy.failed", failed);
    return built_topics - before;
}

typedef const u8 *(__thiscall *fn_filtered)(const u8 *, const u8 *, u32, u32);

/* Test probe: how many topics' summaries pass for one speaker. In verify mode the engine also
 * answers every topic, and the answer's own stub must pass: a failure is a topic the summary would
 * hide. */
static void check_speaker(const u8 *actor, u32 reference, u32 flag, u32 *counts)
{
    u32 i;

    for (i = 0; i < SLOTS; i++) {
        struct topic *t = slots[i];
        const u8 *answer, *info;
        u32 n;

        if (!t)
            continue;
        counts[0]++;
        if (first_passing(t, actor) >= 0)
            counts[1]++;
        if (mode != MODE_VERIFY)
            continue;
        answer = ((fn_filtered)TES3X_VALUE(LAZY_FILTERED))(t->dial, actor, reference, flag);
        if (!answer)
            continue;
        counts[2]++;
        for (n = 0, info = *(const u8 *const *)(t->dial + DIAL_INFO);
             n < t->count && info && info != answer;
             n++, info = *(const u8 *const *)(info + INFO_NEXT))
            ;
        if (n == t->count || !info || !passes(&t->entries[n], actor)) {
            if (counts[3]++ < 8)
                tes3x_log_hex3("lazy.summary_hidden_at", (u32)t->dial, (u32)answer, (u32)actor);
        }
    }
}

static void log_counts(const u32 *counts)
{
    tes3x_log("lazy.summary_topics", counts[0]);
    tes3x_log("lazy.summary_passing", counts[1]);
    tes3x_log("lazy.summary_answered", counts[2]);
    tes3x_log("lazy.summary_hidden", counts[3]);
}

/* The last speaker getFilteredInfo saw. */
u32 tes3x_lazy_dialogue_summary_check(void)
{
    u32 counts[4] = {0};

    if (!last_actor) {
        tes3x_log("lazy.summary_no_speaker", 0);
        return 0;
    }
    check_speaker(last_actor, last_reference, last_flag, counts);
    log_counts(counts);
    return counts[3];
}

/* Every stride-th NPC or creature that a response names as its speaker, with no reference. */
u32 tes3x_lazy_dialogue_summary_sweep(u32 stride)
{
    u32 i, n = 0, speakers = 0, counts[4] = {0};

    for (i = 0; i < OBJECTS; i++) {
        const u8 *object = (const u8 *)objects[i];
        u32 type;

        if (!object)
            continue;
        type = *(const u32 *)(object + OBJECT_TYPE);
        if ((type != TYPE_NPC && type != TYPE_CREA) || n++ % stride)
            continue;
        speakers++;
        check_speaker(object, 0, 0, counts);
    }
    tes3x_log("lazy.summary_speakers", speakers);
    log_counts(counts);
    return counts[3];
}
