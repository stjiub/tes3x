/* Extend Script::RunFunction and count opcode use.
 * `script-ext` widens six instruction-length checks, then redirects three call sites here.
 * Custom opcodes occupy [0x2001, 0x4000); the engine compares the upper bound as signed.
 */

#include "tes3x_thunks.h"
#include "tes3xlog.h"

#ifndef TES3X_RUN_FUNCTION
#error "define TES3X_RUN_FUNCTION to the VA of Script::RunFunction"
#endif
#ifndef TES3X_COMMAND_TABLE
#error "define TES3X_COMMAND_TABLE to the VA of the script command table"
#endif

#define TES3X_STR_(x) #x
#define TES3X_STR(x) TES3X_STR_(x)

#define TES3X_OPCODE_BASE 0x2000
#define TES3X_OPCODE_CEIL 0x4000

enum {
    TES3X_OP_VERSION = 0x2001, /* tes3xGetVersion  -> the payload's build number */
    TES3X_OP_PROFILE = 0x2002, /* tes3xDumpProfile -> write the call counts to the log */
};

#define TES3X_VERSION 1

/* Cover the 445 retail opcodes plus some revision headroom. */
#define TES3X_OP_FIRST 0x1000
#define TES3X_OP_SLOTS 0x200

/* About 30 seconds between dumps at 61 frames per second. */
#define TES3X_PROFILE_EVERY 1800

/* Freestanding floating-point code still requires this CRT marker. */
int _fltused;

/* Written by the hook on every script call, so they are not static - the asm names them. */
u32 tes3x_op_count[TES3X_OP_SLOTS];
u32 tes3x_op_total;

static u32 tes3x_calls;
static u32 tes3x_unknown;
static u32 tes3x_profile_calls;

typedef struct {
    const char *name;
    const char *short_name;
    u32 opcode;
} tes3x_command;

/* Sample the first calls and powers of ten to limit log growth. */
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

/* The zero entry terminates the command table. */
static const char *tes3x_command_name(u32 opcode)
{
    const tes3x_command *c = (const tes3x_command *)TES3X_COMMAND_TABLE;
    for (; c->name; c++)
        if (c->opcode == opcode)
            return c->name;
    return 0;
}

static void tes3x_dump_profile(void)
{
    char tag[64];
    u32 i, distinct = 0;

    tes3x_log("profile.begin", tes3x_op_total);
    for (i = 0; i < TES3X_OP_SLOTS; i++) {
        const char *name;
        u32 n = 0;
        if (!tes3x_op_count[i])
            continue;
        distinct++;
        name = tes3x_command_name(TES3X_OP_FIRST + i);
        tag[n++] = 'o';
        tag[n++] = 'p';
        tag[n++] = '.';
        if (name) {
            while (*name && n < sizeof(tag) - 1)
                tag[n++] = *name++;
        } else {
            /* Preserve unnamed opcodes as numbers. */
            const char *hex = "0123456789ABCDEF";
            u32 op = TES3X_OP_FIRST + i, s;
            for (s = 12; n < sizeof(tag) - 1; s -= 4) {
                tag[n++] = hex[(op >> s) & 0xF];
                if (!s)
                    break;
            }
        }
        tag[n] = 0;
        tes3x_log(tag, tes3x_op_count[i]);
    }
    tes3x_log("profile.end", distinct);
}

/* Returns the custom opcode result through st(0). */
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
    case TES3X_OP_PROFILE:
        tes3x_profile_calls++;
        /* Dump immediately, then at the configured interval. */
        if (tes3x_profile_calls % TES3X_PROFILE_EVERY == 1)
            tes3x_dump_profile();
        return (float)tes3x_profile_calls;
    default:
        tes3x_unknown++;
        if (tes3x_should_log(tes3x_unknown))
            tes3x_log("script.unknown_opcode", opcode);
        return 0.0f;
    }
}

/* __thiscall shim; the callee pops 12 bytes. Vanilla opcodes tail-call the original. */
__attribute__((naked)) void tes3x_script_hook(void)
{
    __asm__ volatile(
        "movl 0x4(%esp), %eax\n\t" /* the opcode argument */
        "incl _tes3x_op_total\n\t"
        "pushl %edx\n\t"
        "movl %eax, %edx\n\t"
        "subl $" TES3X_STR(TES3X_OP_FIRST) ", %edx\n\t"
        "cmpl $" TES3X_STR(TES3X_OP_SLOTS) ", %edx\n\t"
        "jae 2f\n\t" /* unsigned, so an opcode below the block wraps out of range too */
        "incl _tes3x_op_count(,%edx,4)\n\t"
        "2:\n\t"
        "popl %edx\n\t"
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
