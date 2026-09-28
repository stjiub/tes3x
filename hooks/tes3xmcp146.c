/* MCP id=146: cancel a fully drawn bow with the Ready Weapon control.
 *
 * The Xbox player-input loop suppresses its normal action handlers while an attack is in
 * progress.  Hook that guard, recognize Ready Weapon, destroy the nocked projectile exactly as
 * MCP does, and resume after the weapon-toggle handler so the bow stays readied.
 */

#ifndef TES3X_MCP146_ATTACKING
#error "define TES3X_MCP146_ATTACKING to MobileActor::isAttackingOrCasting"
#endif
#ifndef TES3X_MCP146_INPUT
#error "define TES3X_MCP146_INPUT to Input::Action"
#endif
#ifndef TES3X_MCP146_GAME
#error "define TES3X_MCP146_GAME to the global game pointer"
#endif
#ifndef TES3X_MCP146_RESUME
#error "define TES3X_MCP146_RESUME to the input-loop continuation"
#endif

#define TES3X_STR_(x) #x
#define TES3X_STR(x) TES3X_STR_(x)

/* Entered in place of isAttackingOrCasting with the MobilePlayer in ecx and esi.  A successful
 * de-nock replaces this call's return address with the continuation after Ready Weapon handling.
 * The caller's action flag is at esp+0x17 while this call's return address is on the stack.
 */
__attribute__((naked)) void tes3x_mcp146_hook(void)
{
    __asm__ volatile(
        "movl $" TES3X_STR(TES3X_MCP146_ATTACKING) ", %eax\n\t"
        "call *%eax\n\t"
        "testb %al, %al\n\t"
        "jz 3f\n\t"

        /* Xbox accepts both the pressed and held forms used by its Ready Weapon handler. */
        "movl " TES3X_STR(TES3X_MCP146_GAME) ", %eax\n\t"
        "movl 0x4c(%eax), %ecx\n\t"
        "pushl $2\n\t"
        "pushl $6\n\t"
        "movl $" TES3X_STR(TES3X_MCP146_INPUT) ", %eax\n\t"
        "call *%eax\n\t"
        "testl %eax, %eax\n\t"
        "jnz 1f\n\t"
        "movl " TES3X_STR(TES3X_MCP146_GAME) ", %eax\n\t"
        "movl 0x4c(%eax), %ecx\n\t"
        "pushl $1\n\t"
        "pushl $6\n\t"
        "movl $" TES3X_STR(TES3X_MCP146_INPUT) ", %eax\n\t"
        "call *%eax\n\t"
        "testl %eax, %eax\n\t"
        "jz 2f\n\t"

        "1:\n\t"
        "movl 0x100(%esi), %ecx\n\t"
        "testl %ecx, %ecx\n\t"
        "jz 2f\n\t"
        "movl (%ecx), %eax\n\t"
        "pushl $1\n\t"
        "call *(%eax)\n\t"
        "movl $0, 0x100(%esi)\n\t"
        "movb $1, 0x17(%esp)\n\t"
        "movl $" TES3X_STR(TES3X_MCP146_RESUME) ", (%esp)\n\t"

        "2:\n\t"
        "movb $1, %al\n\t"
        "3:\n\t"
        "ret\n\t");
}
