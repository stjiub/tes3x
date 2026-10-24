/* Recolor dialogue topics whose current response the player has already read. */
#include "tes3x_thunks.h"
#include "tes3xnt.h"
#include "tes3xlog.h"

#define NtCreateFile KFN(THUNK_NtCreateFile, fn_NtCreateFile)
#define NtReadFile KFN(THUNK_NtReadFile, fn_NtReadFile)
#define NtWriteFile KFN(THUNK_NtWriteFile, fn_NtWriteFile)
#define NtFlushBuffersFile KFN(THUNK_NtFlushBuffersFile, fn_NtFlushBuffersFile)
#define NtClose KFN(THUNK_NtClose, fn_NtClose)

#define COLOR_FIRST 0xFFFF804Du /* normal, over, pressed, current */
#define COLOR_COUNT 4
#define PROP_COLOR 2
#define INFO_FILE_OFFSET 0x24
#define TABLE_SIZE 8192u /* keys; power of two */
#define FILE_LIMIT (TABLE_SIZE * 4u / 2u)
#define CHILDREN_BEGIN 0x28
#define CHILDREN_END 0x2c
#define HOLDER_REFERENCE 0x14
#define NPC_NAME 0x70
#define MARK_LIMIT 256u
#define TOPIC_LIMIT 256u
#define LIST_HEAD 8
#define NODE_NEXT 4
#define NODE_DATA 8
#define DIALOGUE_NAME 0x10
#define DIM 0.6f

typedef void(__attribute__((thiscall)) *fn_run)(u8 *info, u8 *reference);
typedef void(__cdecl *fn_list)(u8 *holder, u8 *list);
typedef void(__cdecl *fn_update)(void);
typedef void *(__cdecl *fn_find_menu)(u32 id);
typedef u8 *(__attribute__((thiscall)) *fn_find_child)(u8 *, u32);
typedef u32(__cdecl *fn_ui_id)(const char *name);
typedef u8 *(__attribute__((thiscall)) *fn_filter)(u8 *topic, u8 *actor, u8 *reference, u32 flag);
typedef u8 *(__attribute__((thiscall)) *fn_player)(u8 *);
typedef const char *(__attribute__((thiscall)) *fn_text)(u8 *);
typedef u32 *(__attribute__((thiscall)) *fn_get_prop)(u8 *, void *, u32, int, int, int);
typedef void(__attribute__((thiscall)) *fn_set_prop)(u8 *, u32, int, int);
typedef void(__attribute__((thiscall)) *fn_paint)(u8 *);
typedef void(__attribute__((thiscall)) *fn_node_update)(u8 *, int);
typedef u8(__cdecl *fn_state)(u8 *, u32, u32, u32, u32);

static char path[] = "T:\\tes3x-topics.dat";
static u32 table[TABLE_SIZE];
static u32 stored; /* keys in the file, which is append-only */
static int loaded;
static u8 *actor_reference;
static u32 normal[COLOR_COUNT];
static int normal_known;
static u8 *marked[MARK_LIMIT];
static u32 marks;
static const char *topic_name[TOPIC_LIMIT];
static u8 topic_read[TOPIC_LIMIT];
static u32 topics;

static int plausible(const u8 *ptr)
{
    return (u32)ptr >= 0x10000u && (u32)ptr < 0x80000000u && !((u32)ptr & 3);
}

static u32 hash(u32 h, const char *s)
{
    while (s && *s)
        h = (h ^ (u8)*s++) * 16777619u;
    return h;
}

/* Responses read by one character, found by the offset of the INFO in its plugin. */
static u32 key_for(const u8 *info)
{
    u8 *world = *(u8 **)TES3X_TOPIC_WORLD;
    u8 *mobile, *reference, *base;
    u32 h = 2166136261u, offset = *(const u32 *)(info + INFO_FILE_OFFSET);

    if (!offset || !plausible(world))
        return 0;
    mobile = ((fn_player)TES3X_TOPIC_PLAYER)(world);
    reference = plausible(mobile) ? *(u8 **)(mobile + 0x14) : 0;
    base = plausible(reference) ? *(u8 **)(reference + 0x28) : 0;
    h = hash(h, plausible(base) ? *(const char **)(base + NPC_NAME) : 0);
    h = (h ^ offset) * 16777619u;
    h = (h ^ (offset >> 16)) * 16777619u;
    return h ? h : 1;
}

static int find(u32 key, u32 *slot)
{
    u32 i = key & (TABLE_SIZE - 1);

    while (table[i] && table[i] != key)
        i = (i + 1) & (TABLE_SIZE - 1);
    *slot = i;
    return table[i] == key;
}

static void load(void)
{
    static u32 buf[FILE_LIMIT / 4];
    ANSI_STRING name;
    OBJECT_ATTRIBUTES oa;
    IO_STATUS_BLOCK iosb;
    u64 offset = 0;
    void *h = 0;
    u32 i, slot;

    loaded = 1;
    tes3x_dos_attributes(&oa, &name, path);
    if (NtCreateFile(&h, GENERIC_READ | SYNCHRONIZE, &oa, &iosb, 0, FILE_ATTRIBUTE_NORMAL,
                     FILE_SHARE_READ, FILE_OPEN, FILE_SYNCHRONOUS_IO_NONALERT) != 0)
        return;
    if (NtReadFile(h, 0, 0, 0, &iosb, buf, sizeof(buf), &offset) == 0)
        stored = iosb.Information / 4;
    NtClose(h);
    for (i = 0; i < stored; i++)
        if (buf[i] && !find(buf[i], &slot))
            table[slot] = buf[i];
    tes3x_log("topiccolor.loaded", stored);
}

static void append(u32 key)
{
    ANSI_STRING name;
    OBJECT_ATTRIBUTES oa;
    IO_STATUS_BLOCK iosb;
    u64 offset = FILE_WRITE_TO_END_OF_FILE;
    void *h = 0;

    tes3x_dos_attributes(&oa, &name, path);
    if (NtCreateFile(&h, GENERIC_WRITE | SYNCHRONIZE, &oa, &iosb, 0, FILE_ATTRIBUTE_NORMAL,
                     FILE_SHARE_READ, FILE_OPEN_IF, FILE_SYNCHRONOUS_IO_NONALERT) != 0)
        return;
    if (NtWriteFile(h, 0, 0, 0, &iosb, &key, 4, &offset) == 0)
        NtFlushBuffersFile(h, &iosb);
    NtClose(h);
}

static void remember(u32 key)
{
    u32 slot;

    if (!key)
        return;
    if (!loaded)
        load();
    if (find(key, &slot) || stored >= FILE_LIMIT / 4)
        return;
    table[slot] = key;
    stored++;
    append(key);
    tes3x_log("topiccolor.read", stored);
}

static int is_read(u32 key)
{
    u32 slot;

    if (!loaded)
        load();
    return key && find(key, &slot);
}

static int same(const char *a, const char *b)
{
    while (*a && *a == *b) {
        a++;
        b++;
    }
    return *a == *b;
}

void __attribute__((thiscall)) tes3x_topiccolor_run(u8 *info, u8 *reference)
{
    if (plausible(info))
        remember(key_for(info));
    ((fn_run)TES3X_TOPIC_RUN)(info, reference);
}

/* After the engine filters the actor's topics, note which of them have a read response. */
void tes3x_topiccolor_list(u8 *holder, u8 *list)
{
    u8 *node;

    actor_reference = plausible(holder) ? *(u8 **)(holder + HOLDER_REFERENCE) : 0;
    ((fn_list)TES3X_TOPIC_LIST)(holder, list);
    topics = 0;
    for (node = plausible(list) ? *(u8 **)(list + LIST_HEAD) : 0;
         plausible(node) && topics < TOPIC_LIMIT; node = *(u8 **)(node + NODE_NEXT)) {
        u8 *dialogue = *(u8 **)(node + NODE_DATA);
        u8 *info;

        if (!plausible(dialogue) || !plausible(actor_reference))
            continue;
        info = ((fn_filter)TES3X_TOPIC_FILTER)(dialogue, actor_reference, actor_reference, 0);
        topic_name[topics] = *(const char **)(dialogue + DIALOGUE_NAME);
        topic_read[topics] = plausible(info) && is_read(key_for(info));
        topics++;
    }
}

static void read_colors(u8 *widget, u32 *out)
{
    u32 scratch[8] = {0};
    u32 i, *value;

    for (i = 0; i < COLOR_COUNT; i++) {
        value = ((fn_get_prop)TES3X_TOPIC_GET_PROP)(widget, scratch, COLOR_FIRST + i,
                                                    PROP_COLOR, 0, 0);
        out[i] = value ? *value : 0;
    }
}

static void write_colors(u8 *widget, const u32 *colors)
{
    u32 i;

    for (i = 0; i < COLOR_COUNT; i++)
        ((fn_set_prop)TES3X_TOPIC_SET_PROP)(widget, COLOR_FIRST + i, (int)colors[i],
                                            PROP_COLOR);
    ((fn_paint)TES3X_TOPIC_PAINT)(widget);
    ((fn_node_update)TES3X_TOPIC_UPDATE_NODE)(widget, 0);
}

static void dim(u32 *colors)
{
    u32 i;

    for (i = 0; i < 3; i++) {
        float value = *(float *)&colors[i] * DIM;
        colors[i] = *(u32 *)&value;
    }
}

static int is_marked(const u8 *widget)
{
    u32 i;

    for (i = 0; i < marks; i++)
        if (marked[i] == widget)
            return 1;
    return 0;
}

/* Every state change recomputes the color from the palette; keep read topics dim. */
u8 tes3x_topiccolor_state(u8 *widget, u32 a, u32 b, u32 c, u32 d)
{
    u8 result = ((fn_state)TES3X_TOPIC_STATE)(widget, a, b, c, d);
    u32 colors[COLOR_COUNT];

    if (marks && is_marked(widget)) {
        read_colors(widget, colors);
        dim(colors);
        write_colors(widget, colors);
    }
    return result;
}

static int topic_is_read(const char *text)
{
    u32 i;

    for (i = 0; i < topics; i++)
        if (topic_name[i] && same(text, topic_name[i]))
            return topic_read[i];
    return 0;
}

void tes3x_topiccolor_update(void)
{
    fn_ui_id ui_id = (fn_ui_id)TES3X_TOPIC_UI_ID;
    u8 *menu, *pane, **child, **end;
    u32 topic_id;
    u32 shown[COLOR_COUNT];

    ((fn_update)TES3X_TOPIC_UPDATE)();
    marks = 0;
    menu = ((fn_find_menu)TES3X_TOPIC_FIND_MENU)(ui_id("MenuDialog"));
    pane = plausible(menu) ? ((fn_find_child)TES3X_TOPIC_FIND_CHILD)(
        menu, ui_id("MenuDialog_topics_pane")) : 0;
    pane = plausible(pane) ? ((fn_find_child)TES3X_TOPIC_FIND_CHILD)(
        pane, ui_id("PartScrollPane_pane")) : 0;
    if (!plausible(pane))
        return;
    topic_id = ui_id("MenuDialog_a_topic") & 0xFFFF;
    end = *(u8 ***)(pane + CHILDREN_END);
    for (child = *(u8 ***)(pane + CHILDREN_BEGIN); child && child != end; child++) {
        const char *text;

        if (!plausible(*child) || *(unsigned short *)(*child + 0x20) != topic_id)
            continue;
        text = ((fn_text)TES3X_TOPIC_WIDGET_TEXT)(*child);
        if (!text)
            continue;
        if (!normal_known) {
            read_colors(*child, normal);
            normal_known = 1;
            tes3x_log_hex3("topiccolor.normal", normal[0], normal[1], normal[2]);
        }
        shown[0] = normal[0];
        shown[1] = normal[1];
        shown[2] = normal[2];
        shown[3] = normal[3];
        if (topic_is_read(text)) {
            dim(shown);
            if (marks < MARK_LIMIT)
                marked[marks++] = *child;
        }
        write_colors(*child, shown);
    }
}
