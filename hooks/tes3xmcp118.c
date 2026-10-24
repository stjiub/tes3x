/* Keep an airborne drop above ground when placement finds no nearby surface. */

typedef void *(__attribute__((thiscall)) *get_player_fn)(void *);

void mcp118_adjust(float *position)
{
    void *world = *(void **)TES3X_VALUE(MCP118_WORLD_PTR);
    unsigned char *player = ((get_player_fn)TES3X_VALUE(MCP118_GET_PLAYER))(world);
    unsigned char *reference = player ? *(unsigned char **)(player + 0x14) : 0;
    float *player_position;

    if (!reference)
        return;
    player_position = (float *)(reference + 0x38);
    if (player_position[0] - position[0] > 128.0f ||
        position[0] - player_position[0] > 128.0f ||
        player_position[1] - position[1] > 128.0f ||
        position[1] - player_position[1] > 128.0f ||
        player_position[2] - position[2] > 128.0f) {
        position[0] = player_position[0];
        position[1] = player_position[1];
        position[2] = player_position[2];
    }
}

__attribute__((naked)) void tes3x_mcp118_drop_finish(void)
{
    __asm__ volatile(
        "pushl %ecx\n\t"
        "pushl %esi\n\t"
        "call _mcp118_adjust\n\t"
        "addl $4, %esp\n\t"
        "popl %ecx\n\t"
        "movl $" TES3X_SYM(MCP118_RESET) ", %eax\n\t"
        "jmp *%eax\n\t");
}
