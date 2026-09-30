/* Register or unregister moved-actor collision according to destination-cell residency. */

#ifndef TES3X_MCP125_ADD_MOB
#error "define TES3X_MCP125_ADD_MOB to MobManager::addMob"
#endif
#ifndef TES3X_MCP125_REMOVE_MOB
#error "define TES3X_MCP125_REMOVE_MOB to MobManager::removeMob"
#endif

#define TES3X_STR_(x) #x
#define TES3X_STR(x) TES3X_STR_(x)

/* al is the destination-resident flag; ecx and the stack already hold the original call. */
__attribute__((naked)) void tes3x_mcp125_collision_hook(void)
{
    __asm__ volatile(
        "testb %al, %al\n\t"
        "jnz 1f\n\t"
        "pushl $" TES3X_STR(TES3X_MCP125_REMOVE_MOB) "\n\t"
        "ret\n\t"
        "1:\n\t"
        "pushl $" TES3X_STR(TES3X_MCP125_ADD_MOB) "\n\t"
        "ret\n\t");
}
