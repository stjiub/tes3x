/* Put soul gems under Magic and keep enchanted equipment out of that filter. */

typedef void *(__attribute__((thiscall)) *FindChild)(void *, unsigned);
typedef int *(__attribute__((thiscall)) *GetProperty)(void *, int *, unsigned, unsigned, unsigned, unsigned);
typedef int (__attribute__((thiscall)) *GetEnchantment)(void *);
typedef unsigned (__attribute__((cdecl)) *IsSoulGem)(void *);

__attribute__((noinline)) unsigned tes3x_mcp272_filter(
    unsigned included, void *menu, void *item, unsigned magic_id, unsigned property_id)
{
    FindChild find_child = (FindChild)TES3X_VALUE(MCP272_FIND_CHILD);
    GetProperty get_property = (GetProperty)TES3X_VALUE(MCP272_GET_PROPERTY);
    IsSoulGem is_soul_gem = (IsSoulGem)TES3X_VALUE(MCP272_SOUL_GEM);
    void *magic = find_child(menu, *(volatile unsigned short *)magic_id);
    int selected = -1;
    if (*get_property(magic, &selected, *(volatile unsigned *)property_id, 1, 0, 0)) {
        GetEnchantment enchantment = *(GetEnchantment *)((char *)*(void **)item + 0xd0);
        if (enchantment(item) || (is_soul_gem(item) & 0xff))
            included ^= 1;
    }
    return included;
}

__attribute__((naked)) void tes3x_mcp272_barter(void)
{
    __asm__ volatile(
        "pushl $" TES3X_SYM(MCP272_BARTER_PROPERTY) "\n\t"
        "pushl $" TES3X_SYM(MCP272_BARTER_MAGIC) "\n\t"
        "pushl %ebp\n\t"
        "pushl %esi\n\t"
        "movzbl %bl, %eax\n\t"
        "pushl %eax\n\t"
        "call _tes3x_mcp272_filter\n\t"
        "addl $20, %esp\n\t"
        "movb %al, %bl\n\t"
        "pushl $" TES3X_SYM(MCP272_BARTER_RESUME) "\n\t"
        "ret\n\t");
}

__attribute__((naked)) void tes3x_mcp272_inventory(void)
{
    __asm__ volatile(
        "pushl $" TES3X_SYM(MCP272_INVENTORY_PROPERTY) "\n\t"
        "pushl $" TES3X_SYM(MCP272_INVENTORY_MAGIC) "\n\t"
        "pushl %ebp\n\t"
        "pushl %esi\n\t"
        "movzbl %bl, %eax\n\t"
        "pushl %eax\n\t"
        "call _tes3x_mcp272_filter\n\t"
        "addl $20, %esp\n\t"
        "movb %al, %bl\n\t"
        "pushl $" TES3X_SYM(MCP272_INVENTORY_RESUME) "\n\t"
        "ret\n\t");
}
