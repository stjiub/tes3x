/* Let scripted humanoid animations use every layer and named extra bones. */

#include "tes3x_thunks.h"

typedef void(__attribute__((thiscall)) *fn_play_group)(void *, int, int, int, int);
typedef void(__attribute__((thiscall)) *fn_update_idles)(void *);
typedef void *(__attribute__((thiscall)) *fn_get_player)(void *);
typedef void(__attribute__((thiscall)) *fn_anim_idle_update)(void *);

void __attribute__((thiscall)) tes3x_mcp132_play_group(
    void *animation, int group, int layer, int mode, int loops)
{
    int part;
    (void)layer;
    for (part = 0; part != 3; ++part)
        ((fn_play_group)TES3X_MCP132_PLAY_GROUP)(animation, group, part, mode, loops);
}

/* The engine passes a canonical bone first, then the KF's bone name. */
int __attribute__((cdecl)) tes3x_mcp132_bone_match(
    const char *canonical, const char *name)
{
    const char *p, *a, *b;
    for (p = name; *p; ++p) {
        for (a = p, b = canonical; *b && *a == *b; ++a, ++b) {}
        if (!*b)
            return 0;
    }
    return *canonical ? 1 : 0;
}

void __attribute__((thiscall)) tes3x_mcp132_update_idles(void *data)
{
    unsigned char *player;
    void *world;

    ((fn_update_idles)TES3X_MCP132_UPDATE_IDLES)(data);
    world = *(void **)TES3X_MCP132_WORLD;
    if (!world)
        return;
    player = ((fn_get_player)TES3X_MCP132_GET_PLAYER)(world);
    if (player && (*(unsigned int *)(player + 0x10) & 0x10000000u)) {
        void *reference = *(void **)(player + 0x14);
        if (reference)
            ((fn_anim_idle_update)TES3X_MCP132_ANIM_IDLE_UPDATE)(reference);
    }
}
