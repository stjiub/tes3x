/* Test-only trace of Script::ReplaceGlobalsInData cursor advances. */

#include "tes3xlog.h"

#ifndef TES3X_MCP97_TEST_LOOP
#error "define TES3X_MCP97_TEST_LOOP"
#endif
#ifndef TES3X_MCP97_TEST_EXIT
#error "define TES3X_MCP97_TEST_EXIT"
#endif

#define SKIP_MAX 48

#define TES3X_STR_(x) #x
#define TES3X_STR(x) TES3X_STR_(x)

static u32 skips;
static u32 next;

void tes3x_mcp97_test_record(u32 cursor)
{
    u32 skip = cursor - next;

    next = cursor + 1;
    if (skip != 0 && skip < 0x100 && ++skips <= SKIP_MAX)
        tes3x_log_hex("mcp97.skip", (skip << 16) | (cursor & 0xFFFF));
}

__attribute__((naked)) void tes3x_mcp97_test_hook(void)
{
    __asm__ volatile(
        "pushl %ecx\n\t"
        "pushl %ecx\n\t"
        "call _tes3x_mcp97_test_record\n\t"
        "addl $4, %esp\n\t"
        "popl %ecx\n\t"
        "incl %ecx\n\t"
        "testl %ebp, %ebp\n\t"
        "je 1f\n\t"
        "pushl $" TES3X_STR(TES3X_MCP97_TEST_EXIT) "\n\t"
        "ret\n\t"
        "1:\n\t"
        "pushl $" TES3X_STR(TES3X_MCP97_TEST_LOOP) "\n\t"
        "ret\n\t");
}
