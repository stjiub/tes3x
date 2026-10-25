/* Other players on the local map. A marker is a door marker the engine knows nothing about: the
 * same image in the same rectangle, tagged with a door reference of ours. Hovering it makes the
 * engine ask the reference's destination for its name, and that is the player's. [Xbox]
 * MapMarkers=1 turns it on. */
#ifndef TES3X_NET_MAP_MARKER_ID
#error "define the TES3X_NET_MAP_ ids, parent, pixel conversion, setX/setY and update call"
#endif

typedef void *(__attribute__((thiscall)) *fn_map_find_child)(void *widget, u32 id);
typedef void *(__attribute__((thiscall)) *fn_map_create_image)(void *parent, u32 id,
                                                               const char *path, int a0);
typedef void(__attribute__((thiscall)) *fn_map_set_int)(void *widget, int value);
typedef void(__attribute__((thiscall)) *fn_map_set_visible)(void *widget, int on);
typedef void(__attribute__((thiscall)) *fn_map_set_prop)(void *widget, u32 id, int value, int type);
typedef const u32 *(__attribute__((thiscall)) *fn_map_get_prop)(void *widget, void *scratch,
                                                                 u32 id, u32 type, u32 a, u32 b);
typedef void(__cdecl *fn_map_pixels)(float *xyz, int scale);
typedef void(__cdecl *fn_map_update_local)(void *menu, u32 id, u32 a, u32 b, void *again);

#ifndef ENGINE_ID
#define ENGINE_ID(global) ((u32)(int)*(const short *)(global))
#endif
#define MAP_FLAG_TRUE 0xFFFF80BDu
#define MAP_PROP_POINTER 8
#define MAP_PROP_ID 0x10
#define MAP_SCALE 0x200
#define MAP_VISIBLE 0x7E /* Element: shown */
#define MAP_DOOR_LIST 0x44 /* the door reference's attachment list */
#define MAP_NAME_SLOT 8 /* vtable +0x20 */
#define MAP_ATTACH_DESTINATION 5

/* What the engine walks to a destination's name: reference +0x44 -> node (type, next, data) ->
 * data points at a pointer to an object whose vtable slot 8 returns the text. */
static struct {
    const void *const *vtable;
    u32 slot;
} map_object[PEERS];
static const void *map_holder[PEERS];
static struct {
    u32 type;
    const void *next;
    const void *data;
} map_node[PEERS];
static struct {
    u32 pad[MAP_DOOR_LIST / 4];
    const void *attachments;
} map_door[PEERS];
static char map_names[PEERS][IDENTITY_TEXT];
static const void *map_vtable[MAP_NAME_SLOT + 1];

static void *map_marker[PEERS];
static u32 map_flags, map_enabled, map_read, map_hooked, map_shown, map_updates, map_placed;
static struct pose map_local;
static u32 map_have_local;

static const char *__attribute__((thiscall)) map_player_name(void *object, int a, int b)
{
    u32 slot = ((const u32 *)object)[1];

    tes3x_log_hex3("net.map_name", (u32)object, slot, 0);
    (void)a;
    (void)b;
    return slot < PEERS ? map_names[slot] : "";
}

static int map_floor(float f)
{
    int i = (int)f;

    return (float)i > f ? i - 1 : i;
}

static void map_tables(void)
{
    u32 i;

    map_vtable[MAP_NAME_SLOT] = (const void *)map_player_name;
    for (i = 0; i < PEERS; i++) {
        map_object[i].vtable = map_vtable;
        map_object[i].slot = i;
        map_holder[i] = &map_object[i];
        map_node[i].type = MAP_ATTACH_DESTINATION;
        map_node[i].data = &map_holder[i];
        map_door[i].attachments = &map_node[i];
    }
}

/* The marker the engine has not taken for a door: it still carries our reference. */
static int map_ours(void *marker, u32 slot)
{
    u32 scratch[4];
    const u32 *value = ((fn_map_get_prop)TES3X_NET_GET_PROP)(
        marker, scratch, ENGINE_ID(TES3X_NET_MAP_DOOR_REF_ID), MAP_PROP_POINTER, 0, 0);

    return plausible(value) && *value == (u32)&map_door[slot];
}

static const char *map_peer_name(u32 client)
{
    u32 i;

    for (i = 0; i < PEERS; i++)
        if (identities[i].client == client && (identities[i].have & 1) && identities[i].name[0])
            return identities[i].name;
    return "Player";
}

static void map_place(void *marker, const struct pose *p)
{
    float v[3] = {p->x, p->y, p->z};

    if (map_flags & 8)
        v[0] = v[1] = 1.0e9f;

    ((fn_map_pixels)TES3X_NET_MAP_PIXELS)(v, MAP_SCALE);
    ((fn_map_set_int)TES3X_NET_MAP_SET_X)(marker, map_floor(v[0] - 2.5f) - 2);
    ((fn_map_set_int)TES3X_NET_MAP_SET_Y)(marker, map_floor(v[1] + 2.5f) + 2);
}

static void map_refresh(void *menu)
{
    void *parent;
    static u32 logged;
    struct pose p;
    u8 older[ANIM_BYTES];
    float frac;
    const char *name;
    u32 i, shown = 0;

    if (!plausible(menu) || !(parent = ((fn_map_find_child)TES3X_NET_FIND_CHILD)(
                                  menu, ENGINE_ID(TES3X_NET_MAP_PARENT_ID))) ||
        !plausible(parent)) {
        if (logged < 4)
            tes3x_log_hex3("net.map_parent", logged++, (u32)menu, 0);
        return;
    }
    if (logged < 4)
        tes3x_log_hex3("net.map_parent", 0x10 + logged++, (u32)menu, (u32)parent);
    for (i = 0; i < PEERS; i++) {
        name = 0;
        if (peers[i].client && map_have_local && ghost_pose(i, &p, older, &frac) &&
            (p.flags & STATE_IN_WORLD) && same_place(&p, &map_local))
            name = map_peer_name(peers[i].client);
        if (!name) {
            if (map_marker[i] && map_ours(map_marker[i], i))
                ((fn_map_set_visible)TES3X_NET_SET_VISIBLE)(map_marker[i], 0);
            continue;
        }
        if (!map_marker[i] || !map_ours(map_marker[i], i)) {
            map_marker[i] = ((fn_map_create_image)TES3X_NET_CREATE_IMAGE)(
                parent, ENGINE_ID(map_flags & 32 ? TES3X_NET_MAP_TAGGED_ID : TES3X_NET_MAP_MARKER_ID),
                "textures\\door_icon.tga", 0);
            if (!plausible(map_marker[i])) {
                map_marker[i] = 0;
                continue;
            }
            tes3x_log_hex3("net.map_marker", i, (u32)map_marker[i], (u32)parent);
            ((fn_map_set_int)TES3X_NET_SET_WIDTH)(map_marker[i], 3);
            tes3x_log("net.map_step", 1);
            ((fn_map_set_int)TES3X_NET_SET_HEIGHT)(map_marker[i], 3);
            tes3x_log("net.map_step", 2);
            ((fn_map_set_prop)TES3X_NET_SET_PROP)(map_marker[i], ENGINE_ID(TES3X_NET_MAP_MARKER_ID),
                                                  MAP_FLAG_TRUE, MAP_PROP_ID);
            if (!(map_flags & 2))
                ((fn_map_set_prop)TES3X_NET_SET_PROP)(map_marker[i],
                                                      ENGINE_ID(TES3X_NET_MAP_DOOR_REF_ID),
                                                      (int)&map_door[i], MAP_PROP_POINTER);
            /* Tagged: MenuMap::tagMarker leaves it alone, and no door takes it over. */
            if (!(map_flags & 4))
                ((fn_map_set_prop)TES3X_NET_SET_PROP)(
                    map_marker[i], ENGINE_ID(TES3X_NET_MAP_TAGGED_ID), MAP_FLAG_TRUE, MAP_PROP_ID);
        }
        /* Every update clears the marker flag first; hovering needs it set. */
        ((fn_map_set_prop)TES3X_NET_SET_PROP)(map_marker[i], ENGINE_ID(TES3X_NET_MAP_MARKER_ID),
                                              MAP_FLAG_TRUE, MAP_PROP_ID);
        tes3x_log("net.map_step", 3);
        copy((u8 *)map_names[i], (const u8 *)name, IDENTITY_TEXT);
        map_names[i][IDENTITY_TEXT - 1] = 0;
        tes3x_log("net.map_step", 4);
        map_place(map_marker[i], &p);
        tes3x_log("net.map_step", 5);
        ((fn_map_set_visible)TES3X_NET_SET_VISIBLE)(map_marker[i], !(map_flags & 16));
        tes3x_log("net.map_step", 6);
        shown++;
    }
    map_shown = shown;
    map_placed += shown;
}

static void map_update_hook(void *menu, u32 id, u32 a, u32 b, void *again)
{
    ((fn_map_update_local)TES3X_NET_MAP_UPDATE_LOCAL)(menu, id, a, b, again);
    if (!map_updates++)
        tes3x_log_hex3("net.map_update", (u32)menu, id, map_enabled);
    if (map_enabled)
        map_refresh(menu);
}

static void mapmarks_frame(const u8 *state)
{
    char value[8];
    u32 i;

    if (!map_read) {
        tes3x_ini_xbox("MapMarkers", "0", value, sizeof(value));
        for (map_flags = 0, i = 0; value[i] >= '0' && value[i] <= '9'; i++)
            map_flags = map_flags * 10 + (u32)(value[i] - '0');
        map_enabled = map_flags & 1;
        map_read = 1;
        tes3x_log("net.map_markers", map_enabled);
    }
    if (!map_enabled)
        return;
    if (!map_hooked) {
        const u32 site[] = {TES3X_NET_MAP_UPDATE_SITE};

        map_hooked = 1;
        map_tables();
        if (redirect_calls(site, 1, TES3X_NET_MAP_UPDATE_LOCAL, (const void *)map_update_hook))
            map_hooked = 2;
        tes3x_log("net.map_hook", map_hooked);
    }
    read_pose(state, &map_local);
    map_have_local = 1;
}
