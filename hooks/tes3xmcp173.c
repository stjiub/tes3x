/* The script context is not the NPC reference whose rank changed. */

__attribute__((naked)) void tes3x_mcp173_mark_hook(void)
{
    __asm__ volatile(
        "movl 0x6c(%ecx), %ecx\n\t"
        "movl (%ecx), %eax\n\t"
        "pushl $1\n\t"
        "call *0x14(%eax)\n\t"
        "ret\n\t");
}
