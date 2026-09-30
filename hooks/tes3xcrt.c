/* Compiler helpers that MSVC's CRT would supply. Only Monocypher's Argon2 refers to one, and the
 * linker drops Argon2, but it still resolves every reference first. */

typedef unsigned long long u64;

__attribute__((used)) u64 tes3x_urem64(u64 a, u64 b)
{
    u64 bit = 1;

    if (!b)
        return 0;
    while (b < a && !(b >> 63)) {
        b <<= 1;
        bit <<= 1;
    }
    while (bit) {
        if (a >= b)
            a -= b;
        b >>= 1;
        bit >>= 1;
    }
    return a;
}

/* __aullrem(a, b): 64-bit a % b, arguments on the stack, popped by the callee. */
__asm__(".globl __aullrem\n"
        "__aullrem:\n"
        "    pushl 16(%esp)\n"
        "    pushl 16(%esp)\n"
        "    pushl 16(%esp)\n"
        "    pushl 16(%esp)\n"
        "    call _tes3x_urem64\n"
        "    addl $16, %esp\n"
        "    ret $16\n");
