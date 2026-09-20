/* MCP id=140: throttle MenuLoading redraws to one every 50 milliseconds. */

#include "tes3x_thunks.h"
#include "tes3xlog.h"

#ifndef TES3X_MCP140_UPDATE
#error "define TES3X_MCP140_UPDATE to the loading menu update routine"
#endif
#ifndef TES3X_MCP140_PRESENT
#error "define TES3X_MCP140_PRESENT to the loading menu redraw routine"
#endif
#ifndef TES3X_MCP140_TRUE
#error "define TES3X_MCP140_TRUE to the callback's updated cleanup tail"
#endif
#ifndef TES3X_MCP140_FALSE
#error "define TES3X_MCP140_FALSE to the callback's unchanged cleanup tail"
#endif

#define TES3X_STR_(x) #x
#define TES3X_STR(x) TES3X_STR_(x)
#define KFN(slot, type) (*(type *)(slot))

typedef void(__stdcall *fn_KeQuerySystemTime)(u64 *);
#define KeQuerySystemTime KFN(THUNK_KeQuerySystemTime, fn_KeQuerySystemTime)

#define REDRAW_INTERVAL_100NS 500000u

static u64 last_redraw;

static __attribute__((used, noinline)) int should_redraw(void)
{
    u64 now;
    KeQuerySystemTime(&now);
    if (now - last_redraw < REDRAW_INTERVAL_100NS)
        return 0;
    last_redraw = now;
    return 1;
}

/* Entered by jmp after the fill value has been updated, with the loading menu in esi. */
__attribute__((naked)) void tes3x_mcp140_redraw_hook(void)
{
    __asm__ volatile(
        "call _should_redraw\n\t"
        "testl %eax, %eax\n\t"
        "jz 1f\n\t"
        "movl %esi, %ecx\n\t"
        "movl $" TES3X_STR(TES3X_MCP140_UPDATE) ", %eax\n\t"
        "call *%eax\n\t"
        "pushl $1\n\t"
        "movl %esi, %ecx\n\t"
        "movl $" TES3X_STR(TES3X_MCP140_PRESENT) ", %eax\n\t"
        "call *%eax\n\t"
        "pushl $" TES3X_STR(TES3X_MCP140_TRUE) "\n\t"
        "ret\n\t"
        "1:\n\t"
        "pushl $" TES3X_STR(TES3X_MCP140_FALSE) "\n\t"
        "ret\n\t");
}
