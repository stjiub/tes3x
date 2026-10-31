/* MCP id=141: keep compiled function operands intact while parsing expressions. */

__attribute__((naked)) void tes3x_mcp141_token(void)
{
    __asm__ volatile(
        "movzwl 1(%eax), %edx\n\t"
        "subl $0x1000, %edx\n\t"
        "movl $3, %ecx\n\t"
        "cmpl $0x21, %edx\n\t"
        "je 2f\n\t"
        "cmpl $0xa, %edx\n\t"
        "jl 3f\n\t"
        "cmpl $0xf, %edx\n\t"
        "jg 3f\n\t"
        "movl $4, %ecx\n\t"
        "jmp 3f\n\t"
        "2: movl $5, %ecx\n\t"
        "3: movb (%eax), %dl\n\t"
        "movb %dl, (%ebp)\n\t"
        "incl %eax\n\t"
        "incl %ebp\n\t"
        "decl %ecx\n\t"
        "jne 3b\n\t"
        "pushl $" TES3X_SYM(MCP141_TOKEN_RESUME) "\n\t"
        "ret\n\t");
}

__attribute__((naked)) void tes3x_mcp141_scan(void)
{
    __asm__ volatile(
        "cmpw $0x1021, %dx\n\t"
        "movl $3, %edx\n\t"
        "jne 1f\n\t"
        "movl $5, %edx\n\t"
        "1: addl %edx, %ecx\n\t"
        "addl %edx, %eax\n\t"
        "pushl $" TES3X_SYM(MCP141_SCAN_RESUME) "\n\t"
        "ret\n\t");
}

__attribute__((naked)) void tes3x_mcp141_copy(void)
{
    __asm__ volatile(
        "cmpw $0x1021, %ax\n\t"
        "movl $3, %ecx\n\t"
        "jne 1f\n\t"
        "movl $5, %ecx\n\t"
        "1: movb (%esi), %al\n\t"
        "movb %al, (%edx)\n\t"
        "incl %esi\n\t"
        "incl %edx\n\t"
        "decl %ecx\n\t"
        "jne 1b\n\t"
        "pushl $" TES3X_SYM(MCP141_COPY_RESUME) "\n\t"
        "ret\n\t");
}

__attribute__((naked)) void tes3x_mcp141_walk(void)
{
    __asm__ volatile(
        "cmpw $0x1021, %cx\n\t"
        "movl $2, %ecx\n\t"
        "jne 1f\n\t"
        "movl $4, %ecx\n\t"
        "1: addl %ecx, %esi\n\t"
        "addl %ecx, %edi\n\t"
        "addl %ecx, %ebx\n\t"
        "pushl $" TES3X_SYM(MCP141_WALK_RESUME) "\n\t"
        "ret\n\t");
}
