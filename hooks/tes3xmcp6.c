/* Restore and Drain need the active temporary Fortify amount when capping attributes. */

unsigned tes3x_mcp6_fortify[8];
unsigned tes3x_mcp6_saved_stat;
unsigned tes3x_mcp6_saved_base;


__attribute__((naked)) void tes3x_mcp6_reset(void)
{
    __asm__ volatile(
        "movl $0, _tes3x_mcp6_fortify+0\n\t"
        "movl $0, _tes3x_mcp6_fortify+4\n\t"
        "movl $0, _tes3x_mcp6_fortify+8\n\t"
        "movl $0, _tes3x_mcp6_fortify+12\n\t"
        "movl $0, _tes3x_mcp6_fortify+16\n\t"
        "movl $0, _tes3x_mcp6_fortify+20\n\t"
        "movl $0, _tes3x_mcp6_fortify+24\n\t"
        "movl $0, _tes3x_mcp6_fortify+28\n\t"
        "movl 0xc(%edi), %ecx\n\t"
        "movl (%ecx), %eax\n\t"
        "ret\n\t");
}


/* ESI is the attribute index; the effect magnitude is the event's +8 field. */
__attribute__((naked)) void tes3x_mcp6_record_fortify(void)
{
    __asm__ volatile(
        "movl $" TES3X_SYM(MCP6_GET_MOBILE) ", %eax\n\t"
        "call *%eax\n\t"
        "pushl %eax\n\t"
        "testl %eax, %eax\n\t"
        "je 1f\n\t"
        "cmpl $7, %esi\n\t"
        "ja 1f\n\t"
        "movl " TES3X_SYM(MCP6_GAME_PTR) ", %ecx\n\t"
        "testl %ecx, %ecx\n\t"
        "je 1f\n\t"
        "movl $" TES3X_SYM(MCP6_PLAYER_MOBILE) ", %edx\n\t"
        "call *%edx\n\t"
        "cmpl (%esp), %eax\n\t"
        "jne 1f\n\t"
        "movl 0x1c(%esp), %ecx\n\t"
        "movl 0xa0(%ecx), %ecx\n\t"
        "cmpl $0x4c455053, 4(%ecx)\n\t"
        "jne 2f\n\t"
        "cmpb $0, 0x30(%ecx)\n\t"
        "jne 1f\n\t"
        "2:\n\t"
        "movl 0x24(%esp), %ecx\n\t"
        "movl 8(%ecx), %ecx\n\t"
        "addl %ecx, _tes3x_mcp6_fortify(,%esi,4)\n\t"
        "1:\n\t"
        "popl %eax\n\t"
        "ret\n\t");
}


/* EDI is the attribute index; both handlers use EBX for the Statistic. */
__attribute__((naked)) void tes3x_mcp6_prepare(void)
{
    __asm__ volatile(
        "movl $" TES3X_SYM(MCP6_GET_MOBILE) ", %eax\n\t"
        "call *%eax\n\t"
        "movl $0, _tes3x_mcp6_saved_stat\n\t"
        "pushl %eax\n\t"
        "testl %eax, %eax\n\t"
        "je 1f\n\t"
        "cmpl $7, %edi\n\t"
        "ja 1f\n\t"
        "movl " TES3X_SYM(MCP6_GAME_PTR) ", %ecx\n\t"
        "testl %ecx, %ecx\n\t"
        "je 1f\n\t"
        "movl $" TES3X_SYM(MCP6_PLAYER_MOBILE) ", %edx\n\t"
        "call *%edx\n\t"
        "cmpl (%esp), %eax\n\t"
        "jne 1f\n\t"
        "movl (%esp), %eax\n\t"
        "leal (%edi,%edi,2), %edx\n\t"
        "leal 0x254(%eax,%edx,4), %edx\n\t"
        "movl %edx, _tes3x_mcp6_saved_stat\n\t"
        "movl 4(%edx), %ecx\n\t"
        "movl %ecx, _tes3x_mcp6_saved_base\n\t"
        "flds 4(%edx)\n\t"
        "fiaddl _tes3x_mcp6_fortify(,%edi,4)\n\t"
        "fstps 4(%edx)\n\t"
        "1:\n\t"
        "popl %eax\n\t"
        "ret\n\t");
}


__attribute__((naked)) void tes3x_mcp6_finish_drain(void)
{
    __asm__ volatile(
        "movl _tes3x_mcp6_saved_stat, %edx\n\t"
        "testl %edx, %edx\n\t"
        "je 1f\n\t"
        "movl _tes3x_mcp6_saved_base, %ecx\n\t"
        "movl %ecx, 4(%edx)\n\t"
        "1:\n\t"
        "movl (%ebp), %ecx\n\t"
        "ret $0x28\n\t");
}


__attribute__((naked)) void tes3x_mcp6_finish_restore(void)
{
    __asm__ volatile(
        "movl _tes3x_mcp6_saved_stat, %edx\n\t"
        "testl %edx, %edx\n\t"
        "je 1f\n\t"
        "movl _tes3x_mcp6_saved_base, %ecx\n\t"
        "movl %ecx, 4(%edx)\n\t"
        "1:\n\t"
        "testb %al, %al\n\t"
        "ret $0x28\n\t");
}
