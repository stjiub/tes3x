/* Keep the dispose action unavailable while the actor finishes dying. */

#ifndef TES3X_MCP149_DISPOSE_FLAG
#error "define TES3X_MCP149_DISPOSE_FLAG to the contents menu dispose flag"
#endif

__attribute__((naked)) void tes3x_mcp149_menu_state(void)
{
    __asm__ volatile(
        "movb 0xdd(%esi), %al\n\t"
        "cmpb $0x13, %al\n\t"
        "jne 1f\n\t"
        "movb $0, " TES3X_SYM(MCP149_DISPOSE_FLAG) "\n\t"
        "1:\n\t"
        "ret\n\t");
}
