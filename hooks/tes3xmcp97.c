/* MCP id=97: correct the bytecode scan used while initializing saved script data. */

#ifndef TES3X_MCP97_RESUME
#error "define TES3X_MCP97_RESUME to the VA after the replaced scan instructions"
#endif


/* Entered by jmp with eax at the length-prefixed operand and ecx at its opcode. */
__attribute__((naked)) void tes3x_mcp97_scan_hook(void)
{
    __asm__ volatile(
        "movsbl 0x1(%eax), %eax\n\t"
        "addl %eax, %ecx\n\t"
        "incl %ecx\n\t"
        "pushl $" TES3X_SYM(MCP97_RESUME) "\n\t"
        "ret\n\t");
}
