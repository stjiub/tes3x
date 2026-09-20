/* MCP id=154: pad compiled script-data allocations for the reader's dword accesses. */

#ifndef TES3X_MCP154_LOAD_RESUME
#error "define TES3X_MCP154_LOAD_RESUME after the initial allocation-size load"
#endif
#ifndef TES3X_MCP154_RELOAD_RESUME
#error "define TES3X_MCP154_RELOAD_RESUME after the reload allocation-size load"
#endif

#define TES3X_STR_(x) #x
#define TES3X_STR(x) TES3X_STR_(x)

__attribute__((naked)) void tes3x_mcp154_load_hook(void)
{
    __asm__ volatile(
        "movl 0x240(%edi), %eax\n\t"
        "leal 0x4(%eax), %eax\n\t"
        "pushl $" TES3X_STR(TES3X_MCP154_LOAD_RESUME) "\n\t"
        "ret\n\t");
}

__attribute__((naked)) void tes3x_mcp154_reload_hook(void)
{
    __asm__ volatile(
        "movl 0x240(%esi), %edi\n\t"
        "leal 0x4(%edi), %edi\n\t"
        "pushl $" TES3X_STR(TES3X_MCP154_RELOAD_RESUME) "\n\t"
        "ret\n\t");
}
