/* Keep the loading screen's render thread from waiting on its own stop request.
 *
 * LoadScreenManager::start (0xBFBF0 on NTSC) creates the render thread and opens with
 * `while (busy) Sleep(350)`. The frame-end stop path sets busy and waits for the thread to clear
 * it. If the stop lands after the thread has started but before its first call of start, the
 * thread waits on the flag only its own loop can clear, and both threads sleep for good. With the
 * manager's active flag set that call does nothing but wait, so on the render thread it is
 * skipped and the loop sees the stop.
 */

#include "tes3x_thunks.h"
#include "tes3xlog.h"

#ifndef TES3X_LOADSCREEN_PROC
#error "define TES3X_LOADSCREEN_PROC to the render thread procedure"
#endif
#ifndef TES3X_LOADSCREEN_START
#error "define TES3X_LOADSCREEN_START to LoadScreenManager::start"
#endif

#define MANAGER_ACTIVE 0x29

volatile u32 tes3x_loadscreen_thread;
static u32 skipped;

/* Replaces the pointer the render thread is created with: notes the thread, then runs the
 * procedure. The kernel's current thread is at offset 0x28 of the processor control region. */
__attribute__((naked)) void tes3x_loadscreen_thread_hook(void)
{
    __asm__ volatile(
        "movl %fs:0x28, %eax\n\t"
        "movl %eax, _tes3x_loadscreen_thread\n\t"
        "pushl $" TES3X_SYM(LOADSCREEN_PROC) "\n\t"
        "ret\n\t");
}

/* 1 when the call is the render thread's own and would only wait. */
__attribute__((fastcall)) u32 tes3x_loadscreen_gate(unsigned char *manager)
{
    u32 current;

    __asm__ volatile("movl %%fs:0x28, %0" : "=r"(current));
    if (current != tes3x_loadscreen_thread || !manager[MANAGER_ACTIVE])
        return 0;
    tes3x_log("loadscreen.wait_skipped", ++skipped);
    return 1;
}

/* Replaces the render thread's call of LoadScreenManager::start (thiscall, a float and a char
 * on the stack, which the callee pops). */
__attribute__((naked)) void tes3x_loadscreen_wait_hook(void)
{
    __asm__ volatile(
        "pushal\n\t"
        "call @tes3x_loadscreen_gate@4\n\t"
        "movl %eax, 28(%esp)\n\t"
        "popal\n\t"
        "testl %eax, %eax\n\t"
        "jz 1f\n\t"
        "ret $8\n"
        "1:\n\t"
        "pushl $" TES3X_SYM(LOADSCREEN_START) "\n\t"
        "ret\n\t");
}
