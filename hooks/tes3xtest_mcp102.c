/* Test-only wrapper for action flags restored from ACTN save subrecords. */

#include "tes3xlog.h"

#ifndef TES3X_MCP102_TEST_SET_FLAGS
#error "define TES3X_MCP102_TEST_SET_FLAGS"
#endif
#ifndef TES3X_MCP102_TEST_GET_FLAGS
#error "define TES3X_MCP102_TEST_GET_FLAGS"
#endif

typedef void(__attribute__((thiscall)) *fn_set_flags)(void *, u32);
typedef u32(__attribute__((thiscall)) *fn_get_flags)(void *);

static u32 zero_seen;

void __attribute__((thiscall)) tes3x_mcp102_test_hook(void *self, u32 saved_flags)
{
    fn_set_flags set_flags = (fn_set_flags)TES3X_MCP102_TEST_SET_FLAGS;
    fn_get_flags get_flags = (fn_get_flags)TES3X_MCP102_TEST_GET_FLAGS;

    set_flags(self, saved_flags);
    if (saved_flags == 0 && ++zero_seen == 1) {
        tes3x_log("mcp102.saved", saved_flags);
        tes3x_log("mcp102.loaded", get_flags(self));
    }
}
