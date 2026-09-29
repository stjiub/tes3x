/* Cancel NPC casts which would otherwise retain an actor reference after a cell change. */

#include "tes3xlog.h"

#ifndef TES3X_MCP37_GAME
#error "define TES3X_MCP37_GAME to the global game pointer"
#endif
#ifndef TES3X_MCP37_GET_PLAYER
#error "define TES3X_MCP37_GET_PLAYER to WorldController::getMobilePlayer"
#endif
#ifndef TES3X_MCP37_TREE_NEXT
#error "define TES3X_MCP37_TREE_NEXT to std::_Tree::iterator::operator++"
#endif

#define TES3X_STR_(x) #x
#define TES3X_STR(x) TES3X_STR_(x)

typedef void *(__attribute__((thiscall)) *fn_get_player)(void *);
typedef void(__attribute__((thiscall)) *fn_tree_next)(void **);

void tes3x_mcp37_fix(void)
{
    fn_get_player get_player = (fn_get_player)TES3X_MCP37_GET_PLAYER;
    fn_tree_next tree_next = (fn_tree_next)TES3X_MCP37_TREE_NEXT;
    char *game = *(char **)TES3X_MCP37_GAME;
    char *magic;
    char *player;
    void *player_ref;
    void *end;
    void *node;
    u32 cancelled = 0;

    if (!game)
        return;
    /* Xbox's game layout puts the PC build's +0x70 magic manager at +0x6c. */
    magic = *(char **)(game + 0x6c);
    player = (char *)get_player(game);
    if (!magic || !player)
        return;

    player_ref = *(void **)(player + 0x14);
    end = *(void **)(magic + 0x0c);
    if (!end)
        return;

    node = *(void **)end;
    while (node != end) {
        char *cast = *(char **)((char *)node + 0x10);
        if (cast && *(unsigned char *)(cast + 0xb4) == 0
                && player_ref != *(void **)(cast + 0xb8)) {
            *(unsigned char *)(cast + 0xb4) = 7;
            cancelled++;
        }
        tree_next(&node);
    }
    if (cancelled)
        tes3x_log("mcp37.cancelled", cancelled);
}

/* Replaces `mov ecx,[game]`. Preserve every other register and the incoming flags. */
__attribute__((naked)) void tes3x_mcp37_hook(void)
{
    __asm__ volatile(
        "pushal\n\t"
        "pushfl\n\t"
        "call _tes3x_mcp37_fix\n\t"
        "popfl\n\t"
        "popal\n\t"
        "movl " TES3X_STR(TES3X_MCP37_GAME) ", %ecx\n\t"
        "ret\n\t");
}
