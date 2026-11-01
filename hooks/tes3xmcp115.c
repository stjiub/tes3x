/* Clear paid theft records and leave dead owners' items unmarked. */

#define OBJECT_FACTION 0x54434146u
#define ACTOR_RESPAWNS 4u

typedef void *(__attribute__((thiscall)) *stolen_list_fn)(void *);
typedef void (__attribute__((thiscall)) *clear_list_fn)(void *);
typedef int (__attribute__((thiscall)) *kill_count_fn)(void *, void *);
typedef void (__attribute__((thiscall)) *mark_stolen_fn)(void *, void *);

void tes3x_mcp115_clear_item(void *item)
{
    void **vtable = *(void ***)item;
    void *list = ((stolen_list_fn)vtable[0x148 / 4])(item);
    if (list)
        ((clear_list_fn)TES3X_VALUE(MCP115_CLEAR))(list);
}

/* A faction has no death count, and a respawning actor's never ends its ownership. */
static int owner_dead(void *owner)
{
    char *world = *(char **)TES3X_VALUE(MCP115_WORLD);
    void *counter;

    if (*(unsigned *)((char *)owner + 4) == OBJECT_FACTION
        || (*(unsigned char *)((char *)owner + 0x34) & ACTOR_RESPAWNS) || !world)
        return 0;
    counter = *(void **)(world + 0x60);
    return counter && ((kill_count_fn)TES3X_VALUE(MCP115_KILL_COUNT))(counter, owner) != 0;
}

void tes3x_mcp115_pickup_item(void *item_data, void *item, unsigned exempt)
{
    void *owner;

    if (exempt)
        return;
    owner = *(void **)((char *)item_data + 4);
    if (owner && !owner_dead(owner))
        ((mark_stolen_fn)TES3X_VALUE(MCP115_MARK))(item, owner);
}

__attribute__((naked)) void tes3x_mcp115_guard_hook(void)
{
    __asm__ volatile(
        "pushfl\n\t"
        "pushal\n\t"
        "pushl 0x4(%esi)\n\t"
        "call _tes3x_mcp115_clear_item\n\t"
        "addl $4, %esp\n\t"
        "popal\n\t"
        "popfl\n\t"
        "movl (%esi), %ecx\n\t"
        "movl 0x4(%esi), %edx\n\t"
        "ret\n\t");
}

__attribute__((naked)) void tes3x_mcp115_pickup_hook(void)
{
    __asm__ volatile(
        "pushfl\n\t"
        "pushal\n\t"
        "movzbl %cl, %eax\n\t"
        "pushl %eax\n\t"
        "pushl %edi\n\t"
        "pushl %esi\n\t"
        "call _tes3x_mcp115_pickup_item\n\t"
        "addl $12, %esp\n\t"
        "popal\n\t"
        "popfl\n\t"
        "ret\n\t");
}
