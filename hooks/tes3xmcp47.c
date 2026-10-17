/* Only the player's unequip may change the player's magic menu selection. */

#ifndef TES3X_MCP47_DESELECT
#error "define TES3X_MCP47_DESELECT to UI::MenuMagic_deselectMagic"
#endif
#ifndef TES3X_MCP47_WORLD
#error "define TES3X_MCP47_WORLD to the global world pointer"
#endif
#ifndef TES3X_MCP47_GET_PLAYER
#error "define TES3X_MCP47_GET_PLAYER to the player getter"
#endif

/* Replaces the menu call in Actor::unequipItem; ebx holds its MobileActor. */
__attribute__((naked)) void tes3x_mcp47_deselect(void)
{
    __asm__ volatile(
        "movl " TES3X_SYM(MCP47_WORLD) ", %ecx\n\t"
        "movl $" TES3X_SYM(MCP47_GET_PLAYER) ", %eax\n\t"
        "call *%eax\n\t"
        "cmpl %eax, %ebx\n\t"
        "jne 1f\n\t"
        "movl $" TES3X_SYM(MCP47_DESELECT) ", %eax\n\t"
        "call *%eax\n\t"
        "1:\n\t"
        "ret\n\t");
}
