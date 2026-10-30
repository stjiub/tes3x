/* MCP 145: choose a brewed potion's appearance from its base strength. */

/* The replaced rand call is followed by remainder modulo six. The float at the
 * caller's esp+0x30 is the same strength used to calculate potion value.
 * The call pushes a return address, leaving it at esp+0x34 here. */
__attribute__((naked)) int tes3x_mcp145_appearance(void)
{
    __asm__ volatile(
        "movl 0x34(%esp), %eax\n\t"
        "testl %eax, %eax\n\t"
        "js 1f\n\t"
        "cmpl $0x41700000, %eax\n\t" /* 15.0: bargain */
        "jb 1f\n\t"
        "cmpl $0x41f00000, %eax\n\t" /* 30.0: cheap */
        "jb 2f\n\t"
        "cmpl $0x42340000, %eax\n\t" /* 45.0: standard */
        "jb 3f\n\t"
        "cmpl $0x42700000, %eax\n\t" /* 60.0: quality */
        "jb 4f\n\t"
        "movl $3, %eax\n\t"        /* exclusive */
        "ret\n\t"
        "1: movl $1, %eax\n\t"
        "ret\n\t"
        "2: movl $2, %eax\n\t"
        "ret\n\t"
        "3: xorl %eax, %eax\n\t"
        "ret\n\t"
        "4: movl $5, %eax\n\t"
        "ret\n\t");
}
