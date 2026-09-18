/* Add script commands the retail engine has no opcode for.
 *
 * The engine dispatches a script function through Script::RunFunction: opcode in the first stack
 * argument, `lea edx,[ecx-0x1000]; cmp edx,0x1BC`, then an indirect jump through a 445-entry
 * table with no unused slots. It has three call sites and no indirect references, so standing in
 * front of it is a call-site rewrite rather than a trampoline.
 *
 * What actually blocks a new opcode is elsewhere: six routines bound the opcode to
 * [0x1000, 0x11BD) purely to decide an instruction length - three bytes on a hit, one on a miss.
 * An unknown opcode is not rejected, it is *mis-measured*, and the rest of the bytecode line
 * desyncs. tes3x_patch.py's `script-ext` raises those six bounds to TES3X_OPCODE_CEIL; this file only
 * has to answer for the opcodes above TES3X_OPCODE_BASE once they arrive.
 *
 * The bytecode for a call is the token byte 'X' followed by a little-endian 16-bit opcode. Our
 * opcodes start at 0x2001 rather than 0x2000 so neither byte is NUL: a compiled line is
 * NUL-terminated, and keeping the operand printable-ish means a stray naive scan cannot cut a
 * line in half. The bound comparisons are signed, so nothing may reach 0x8000.
 */

#include "tes3x_thunks.h"
#include "tes3xlog.h"

#ifndef TES3X_RUN_FUNCTION
#error "define TES3X_RUN_FUNCTION to the VA of Script::RunFunction"
#endif

#define TES3X_STR_(x) #x
#define TES3X_STR(x) TES3X_STR_(x)

#define TES3X_OPCODE_BASE 0x2000
#define TES3X_OPCODE_CEIL 0x4000

enum {
    TES3X_OP_VERSION = 0x2001, /* tes3xGetVersion -> the payload's build number */
};

#define TES3X_VERSION 1

/* Any translation unit that touches floating point emits a reference to __fltused, which the CRT
 * would normally supply. There is no CRT here. */
int _fltused;

/* Logging every frame would fill the partition, so the interesting counts are sampled: the
 * first few calls in full, then only powers of ten. */
static u32 tes3x_calls;
static u32 tes3x_unknown;

static int tes3x_should_log(u32 n)
{
    u32 p;
    if (n <= 4)
        return 1;
    for (p = 10; p <= 1000000; p *= 10)
        if (n == p)
            return 1;
    return 0;
}

/* Runs for opcodes at or above TES3X_OPCODE_BASE only; everything else never reaches here.
 * Returns the script-visible result, which the engine reads out of st(0). */
float tes3x_script_run(void *self, u32 opcode, u32 a2, u32 a3)
{
    (void)self;
    (void)a2;
    (void)a3;

    tes3x_calls++;
    switch (opcode) {
    case TES3X_OP_VERSION:
        if (tes3x_should_log(tes3x_calls))
            tes3x_log("script.version_call", tes3x_calls);
        return (float)TES3X_VERSION;
    default:
        tes3x_unknown++;
        if (tes3x_should_log(tes3x_unknown))
            tes3x_log("script.unknown_opcode", opcode);
        return 0.0f;
    }
}

/* Stands in for Script::RunFunction at all three of its call sites.
 *
 * __thiscall: this in ecx, three stack arguments the callee pops (the original ends `ret 0xC`).
 * Both paths are tail calls, so neither disturbs the frame the engine built - vanilla opcodes
 * reach the original with the stack exactly as it found it. */
__attribute__((naked)) void tes3x_script_hook(void)
{
    __asm__ volatile(
        "movl 0x4(%esp), %eax\n\t" /* the opcode argument */
        "cmpl $" TES3X_STR(TES3X_OPCODE_BASE) ", %eax\n\t"
        "jb 1f\n\t"
        "cmpl $" TES3X_STR(TES3X_OPCODE_CEIL) ", %eax\n\t"
        "jae 1f\n\t"
        "pushl 0xC(%esp)\n\t" /* a3, a2, opcode - each shifted by the pushes before it */
        "pushl 0xC(%esp)\n\t"
        "pushl 0xC(%esp)\n\t"
        "pushl %ecx\n\t" /* this */
        "call _tes3x_script_run\n\t"
        "addl $0x10, %esp\n\t"
        "ret $0xC\n\t"
        "1:\n\t"
        "movl $" TES3X_STR(TES3X_RUN_FUNCTION) ", %eax\n\t"
        "jmp *%eax\n\t");
}
