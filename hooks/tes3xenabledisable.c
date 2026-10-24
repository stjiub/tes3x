/* Enable and Disable set a flag in the script's local variables, which a script may not have. */

__attribute__((naked)) void tes3x_enable_flag_hook(void)
{
    __asm__ volatile(
        "movl " TES3X_SYM(ENABLE_DISABLE_LOCALS) ", %eax\n\t"
        "testl %eax, %eax\n\t"
        "jz 1f\n\t"
        "andb $0xfe, 0xc(%eax)\n"
        "1:\n\t"
        "ret\n\t");
}

__attribute__((naked)) void tes3x_disable_flag_hook(void)
{
    __asm__ volatile(
        "movl " TES3X_SYM(ENABLE_DISABLE_LOCALS) ", %eax\n\t"
        "testl %eax, %eax\n\t"
        "jz 1f\n\t"
        "orb $0x1, 0xc(%eax)\n"
        "1:\n\t"
        "ret\n\t");
}
