/* Clear the colour buffers once the renderer exists.  The engine presents its first frame before
 * it clears anything, so a boot without the retail launcher, which leaves a persisted frame,
 * shows uninitialised video memory.
 */

#include "tes3x_thunks.h"

#ifndef TES3X_BOOTCLEAR_CLEAR
#error "define TES3X_BOOTCLEAR_CLEAR to NiXBoxRenderer::clear"
#endif
#ifndef TES3X_BOOTCLEAR_DISPLAY
#error "define TES3X_BOOTCLEAR_DISPLAY to NiXBoxRenderer::display"
#endif
#ifndef TES3X_BOOTCLEAR_NEXT
#error "define TES3X_BOOTCLEAR_NEXT to the function Game::createRenderer calls after create"
#endif


/* Replaces Game::createRenderer's call after NiXBoxRenderer::create, with the renderer in esi.
 * Clears to opaque black, not the renderer's own grey, and presents twice, so both buffers of a
 * double-buffered chain are black.  The next
 * function takes its argument on the stack, so it is entered by a jump.
 */
__attribute__((naked)) void tes3x_bootclear_hook(void)
{
    __asm__ volatile(
        "pushal\n\t"
        "movl 0x2dc(%esi), %edi\n\t"
        "movl $0xff000000, 0x2dc(%esi)\n\t"
        "movl $2, %ebx\n"
        "1:\n\t"
        "pushl $1\n\t"
        "pushl $0\n\t"
        "movl %esi, %ecx\n\t"
        "movl $" TES3X_SYM(BOOTCLEAR_CLEAR) ", %eax\n\t"
        "call *%eax\n\t"
        "movl %esi, %ecx\n\t"
        "movl $" TES3X_SYM(BOOTCLEAR_DISPLAY) ", %eax\n\t"
        "call *%eax\n\t"
        "decl %ebx\n\t"
        "jnz 1b\n\t"
        "movl %edi, 0x2dc(%esi)\n\t"
        "popal\n\t"
        "pushl $" TES3X_SYM(BOOTCLEAR_NEXT) "\n\t"
        "ret\n\t");
}
