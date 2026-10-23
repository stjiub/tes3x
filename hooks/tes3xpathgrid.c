/* Pathgrid lookup for Wander and Flee: a grid with no nodes counts as no grid. */

__attribute__((naked)) void tes3x_pathgrid_zero_hook(void)
{
    __asm__ volatile(
        "movl 0x70(%ecx), %eax\n\t"
        "testl %eax, %eax\n\t"
        "jz 1f\n\t"
        "cmpw $0, 0x1e(%eax)\n\t"
        "je 2f\n\t"
        "cmpl $0, 0x24(%eax)\n\t"
        "je 2f\n"
        "1:\n\t"
        "ret\n"
        "2:\n\t"
        "xorl %eax, %eax\n\t"
        "ret\n\t");
}
