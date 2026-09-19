/* MCP id=1, load half: the restamp fallback at 0x00129CE5.
 * Three failed resolutions and one legitimate runtime-created reference land on the same
 * instruction; bl is 0 only for the legitimate one. A failed resolution restamps the
 * reference with the reading file's index, which is 0 for a savegame and means "created at
 * runtime", so the reference is orphaned and never resolves again.
 */

#include "tes3x_thunks.h"
#include "tes3xlog.h"

#ifndef TES3X_REF_RESUME
#error "define TES3X_REF_RESUME to the VA after the restamp fallback's first instruction"
#endif
#ifndef TES3X_REF_SKIP
#error "define TES3X_REF_SKIP to the VA of the loader's own skip tail"
#endif
#ifndef TES3X_INI_GET_STRING
#error "define TES3X_INI_GET_STRING to the VA of the ini string reader"
#endif
#ifndef TES3X_INI_PATH
#error "define TES3X_INI_PATH to the VA of the engine's ini filename string"
#endif

#define TES3X_STR_(x) #x
#define TES3X_STR(x) TES3X_STR_(x)

/* __cdecl: 0x001933E0 ends `mov esp,ebp; pop ebp; ret`, so the caller clears the arguments. */
typedef int(__cdecl *fn_ini_get_string)(const char *section, const char *key, const char *dflt,
                                        char *buf, int size, const char *file);

/* MCP drops unconditionally - clean saving. This is an escape hatch, not MCP's Keep Replaced
 * Refs, which is a separate save-side mode; [Xbox] DropReplacedRefs=0 restores vanilla's
 * restamp, orphan and all. */
#define DROP_DEFAULT 1

/* The hook reads these; the asm names them. */
u32 tes3x_ref_drop = DROP_DEFAULT;
u32 tes3x_ref_legit;

static u32 orphan_seen;
static int drop_ready;

/* Sample the first hits and powers of ten to limit log growth. */
static int should_log(u32 n)
{
    u32 p;
    if (n <= 4)
        return 1;
    for (p = 10; p <= 1000000; p *= 10)
        if (n == p)
            return 1;
    return 0;
}

/* Deferred: D: is not mounted at the XBE entry point. */
static void load_drop(void)
{
    fn_ini_get_string get = (fn_ini_get_string)TES3X_INI_GET_STRING;
    char buf[16];
    int i;

    drop_ready = 1;
    for (i = 0; i < (int)sizeof(buf); i++)
        buf[i] = 0;

    get("Xbox", "DropReplacedRefs", "", buf, (int)sizeof(buf) - 1, (const char *)TES3X_INI_PATH);
    for (i = 0; buf[i] == ' ' || buf[i] == '\t'; i++)
        ;
    if (buf[i] == '0' || buf[i] == '1') {
        tes3x_ref_drop = (u32)(buf[i] - '0');
        tes3x_log("refs.drop_ini", tes3x_ref_drop);
    } else {
        tes3x_log("refs.drop_default", tes3x_ref_drop);
    }
}

/* Record the first legitimate reference load. */
void tes3x_ref_first(void)
{
    tes3x_log("refs.first", tes3x_ref_legit);
}

void tes3x_ref_orphan(void)
{
    if (!drop_ready)
        load_drop();

    orphan_seen++;
    if (should_log(orphan_seen)) {
        tes3x_log("refs.orphan", orphan_seen);
        tes3x_log("refs.runtime", tes3x_ref_legit);
    }
}

/* Entered by jmp, so esp still holds the loader's frame.
 * Dropping rejoins vanilla's own skip tail with esi cleared: no reference to re-register,
 * the following subrecords still get consumed, and the loop advances normally.
 */
__attribute__((naked)) void tes3x_ref_load_hook(void)
{
    __asm__ volatile(
        "testb %bl, %bl\n\t"
        "jnz 2f\n\t"
        "incl _tes3x_ref_legit\n\t"
        "cmpl $1, _tes3x_ref_legit\n\t"
        "jne 1f\n\t"
        "pushal\n\t"
        "pushfl\n\t"
        "call _tes3x_ref_first\n\t"
        "popfl\n\t"
        "popal\n\t"
        "1:\n\t"
        "movl 0x4dc(%ebp), %eax\n\t" /* the instruction the patch replaced */
        "pushl $" TES3X_STR(TES3X_REF_RESUME) "\n\t"
        "ret\n\t"
        "2:\n\t"
        "pushal\n\t"
        "pushfl\n\t"
        "call _tes3x_ref_orphan\n\t"
        "popfl\n\t"
        "popal\n\t"
        "cmpl $0, _tes3x_ref_drop\n\t"
        "je 1b\n\t"
        "xorl %esi, %esi\n\t"
        "movl 0x18(%esp), %edi\n\t" /* the cell, which the skip tail calls through */
        "movb $0x0, 0x12(%esp)\n\t" /* do not create a reference */
        "movb $0x1, 0x13(%esp)\n\t" /* take the skip branch */
        "movb $0x0, 0x20(%esp)\n\t"
        "pushl $" TES3X_STR(TES3X_REF_SKIP) "\n\t"
        "ret\n\t");
}
