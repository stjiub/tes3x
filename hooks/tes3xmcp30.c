/* Correct bow animation angles and normalize the player projectile launch matrix. */

#include "tes3xlog.h"

#ifndef TES3X_MCP30_MATRIX_MULTIPLY
#error "define TES3X_MCP30_MATRIX_MULTIPLY to the original matrix routine"
#endif

const float tes3x_mcp30_angle = 0.3472963571548462f;

__attribute__((naked)) void tes3x_mcp30_launch_matrix(void)
{
    __asm__ volatile(
        "flds (%ecx)\n\t"
        "flds 4(%ecx)\n\t"
        "fld %st(1)\n\t"
        "fmul %st(0), %st\n\t"
        "fld %st(1)\n\t"
        "fmul %st(0), %st\n\t"
        "faddp %st, %st(1)\n\t"
        "fsqrt\n\t"
        "fld1\n\t"
        "fdivp %st, %st(1)\n\t"
        "fxch %st(2)\n\t"
        "fmul %st(2), %st\n\t"
        "fstps (%ecx)\n\t"
        "fmul %st(1), %st\n\t"
        "fstps 4(%ecx)\n\t"
        "flds 12(%ecx)\n\t"
        "fmul %st(1), %st\n\t"
        "fstps 12(%ecx)\n\t"
        "flds 16(%ecx)\n\t"
        "fmulp %st, %st(1)\n\t"
        "fstps 16(%ecx)\n\t"
        "fld1\n\t"
        "fstps 32(%ecx)\n\t"
        "movl $" TES3X_SYM(MCP30_MATRIX_MULTIPLY) ", %eax\n\t"
        "jmp *%eax\n\t");
}
