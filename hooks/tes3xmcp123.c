/* Mark PlaceItem's destination cell changed before the engine inserts the new reference. */

#ifndef TES3X_MCP123_ADD_REFERENCE
#error "define TES3X_MCP123_ADD_REFERENCE to Cell::addReference"
#endif

#define TES3X_STR_(x) #x
#define TES3X_STR(x) TES3X_STR_(x)

/* The original __thiscall arguments are already in ecx and on the stack. */
__attribute__((naked)) void tes3x_mcp123_add_hook(void)
{
    __asm__ volatile(
        "orb $2, 0x8(%ecx)\n\t"
        "pushl $" TES3X_STR(TES3X_MCP123_ADD_REFERENCE) "\n\t"
        "ret\n\t");
}
