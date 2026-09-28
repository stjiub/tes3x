/* Load only game settings in the process that shows the main menu. New Game and Load relaunch the
 * title, and the relaunched process loads every record again, so the menu's copy is never used.
 */

#include "tes3xlog.h"

#ifndef TES3X_LEAN_LOAD_RECORD
#error "define TES3X_LEAN_LOAD_RECORD to the VA of the load-one-record function"
#endif
#ifndef TES3X_LEAN_RECORD_TAG
#error "define TES3X_LEAN_RECORD_TAG to the VA of TES3File::getFirstSubrecord"
#endif
#ifndef TES3X_LEAN_CELL_BY_NAME
#error "define TES3X_LEAN_CELL_BY_NAME to the VA of the find-cell-by-name function"
#endif
#ifndef TES3X_LEAN_CREATE_PLAYER
#error "define TES3X_LEAN_CREATE_PLAYER to the VA of the create-player function"
#endif
#ifndef TES3X_LEAN_LAUNCH_INFO
#error "define TES3X_LEAN_LAUNCH_INFO to the VA of the launch-info pointer"
#endif
#ifndef TES3X_LEAN_NO_REBOOT
#error "define TES3X_LEAN_NO_REBOOT to the VA of the No Reboot On New Game byte"
#endif

#define TES3X_STR_(x) #x
#define TES3X_STR(x) TES3X_STR_(x)

#define BXWM 0x4D575842
#define TAG_TES3 0x33534554
#define TAG_GMST 0x54534D47

typedef u32(__thiscall *fn_record_tag)(void *);

int tes3x_lean = -1;
static u32 skipped;

/* 1 to skip the current record of file. */
int tes3x_lean_skip(void *file)
{
    u32 tag;

    if (tes3x_lean < 0) {
        u32 *info = *(u32 **)TES3X_LEAN_LAUNCH_INFO;
        u8 *no_reboot = (u8 *)TES3X_LEAN_NO_REBOOT; /* New Game, then Load */
        /* Without a relaunch the menu's records are the game's. */
        tes3x_lean = !(info && info[0] == BXWM) && !no_reboot[0] && !no_reboot[1];
        tes3x_log("lean.menu", tes3x_lean);
    }
    if (!tes3x_lean)
        return 0;
    tag = ((fn_record_tag)TES3X_LEAN_RECORD_TAG)(file);
    if (tag == TAG_GMST || tag == TAG_TES3)
        return 0;
    if (++skipped % 16384 == 0)
        tes3x_log("lean.skipped", skipped);
    return 1;
}

/* __thiscall shim for the load-one-record call: the file and two flags on the stack, callee
 * pops them, nonzero on success. A skipped record reports success; nextForm seeks past it. */
__attribute__((naked)) void tes3x_lean_record_hook(void)
{
    __asm__ volatile(
        "pushl %ecx\n\t"
        "pushl 0x8(%esp)\n\t" /* the file */
        "call _tes3x_lean_skip\n\t"
        "addl $0x4, %esp\n\t"
        "popl %ecx\n\t"
        "testl %eax, %eax\n\t"
        "jz 1f\n\t"
        "ret $0xc\n\t" /* eax is 1 */
        "1:\n\t"
        "pushl $" TES3X_STR(TES3X_LEAN_LOAD_RECORD) "\n\t"
        "ret\n\t");
}

/* The menu is shown only if the [PreLoad] Cell 0 lookup succeeds, and its result is discarded
 * right after, so without cells report any nonzero pointer. __thiscall, one argument. */
__attribute__((naked)) void tes3x_lean_preload_hook(void)
{
    __asm__ volatile(
        "cmpl $1, _tes3x_lean\n\t"
        "jne 1f\n\t"
        "movl $1, %eax\n\t"
        "ret $0x4\n\t"
        "1:\n\t"
        "pushl $" TES3X_STR(TES3X_LEAN_CELL_BY_NAME) "\n\t"
        "ret\n\t");
}

/* New Game creates the player before relaunching, which needs the player's NPC record. The
 * relaunched process creates its own. No arguments. */
__attribute__((naked)) void tes3x_lean_player_hook(void)
{
    __asm__ volatile(
        "cmpl $1, _tes3x_lean\n\t"
        "jne 1f\n\t"
        "ret\n\t"
        "1:\n\t"
        "pushl $" TES3X_STR(TES3X_LEAN_CREATE_PLAYER) "\n\t"
        "ret\n\t");
}
