/* Lower the first-person bow and arms while a projectile is nocked.  The world camera and
 * projectile direction are unchanged; this adjusts only the completed viewmodel transform.
 */

#include "tes3xlog.h"

#ifndef TES3X_BOW_VIEW_UPDATE
#error "define TES3X_BOW_VIEW_UPDATE to MobilePlayer::update1stPersonTransform"
#endif
#ifndef TES3X_INI_GET_STRING
#error "define TES3X_INI_GET_STRING to the VA of the ini string reader"
#endif
#ifndef TES3X_INI_PATH
#error "define TES3X_INI_PATH to the VA of the engine's ini filename string"
#endif

#define TES3X_STR_(x) #x
#define TES3X_STR(x) TES3X_STR_(x)

typedef int(__cdecl *fn_ini_get_string)(const char *, const char *, const char *,
                                        char *, int, const char *);

#define DEFAULT_OFFSET_Z (-12)
#define MIN_OFFSET_Z (-64)
#define MAX_OFFSET_Z 64

static int offset_z = DEFAULT_OFFSET_Z;
static int offset_ready;

/* Deferred because D: is not mounted at the XBE entry point. */
static void load_offset(void)
{
    fn_ini_get_string get = (fn_ini_get_string)TES3X_INI_GET_STRING;
    char buf[16];
    int i, sign = 1, value = 0, set = 0;

    offset_ready = 1;
    for (i = 0; i < (int)sizeof(buf); i++)
        buf[i] = 0;
    get("Xbox", "BowViewOffsetZ", "", buf, (int)sizeof(buf) - 1,
        (const char *)TES3X_INI_PATH);
    for (i = 0; buf[i] == ' ' || buf[i] == '\t'; i++)
        ;
    if (buf[i] == '-' || buf[i] == '+') {
        if (buf[i++] == '-')
            sign = -1;
    }
    while (buf[i] >= '0' && buf[i] <= '9') {
        value = value * 10 + (buf[i++] - '0');
        set = 1;
    }
    if (set) {
        value *= sign;
        if (value < MIN_OFFSET_Z)
            value = MIN_OFFSET_Z;
        if (value > MAX_OFFSET_Z)
            value = MAX_OFFSET_Z;
        offset_z = value;
    }
    tes3x_log_hex(set ? "bow.offset_z_ini" : "bow.offset_z", (u32)offset_z);
}

void *__stdcall tes3x_bow_view_target(void *player)
{
    char *root;

    if (!*(void **)((char *)player + 0x100))
        return 0;
    if (!offset_ready)
        load_offset();
    root = *(char **)((char *)player + 0x660);
    if (!root)
        return 0;
    root = *(char **)(root + 0x10);
    return root ? root + 0x38 : 0;
}

/* The call site keeps MobilePlayer in esi. Preserve the original routine's register and flag
 * results while changing only the transform it just wrote.
 */
__attribute__((naked)) void tes3x_bow_view_hook(void)
{
    __asm__ volatile(
        "movl $" TES3X_STR(TES3X_BOW_VIEW_UPDATE) ", %eax\n\t"
        "call *%eax\n\t"
        "pushfl\n\t"
        "pushal\n\t"
        "pushl %esi\n\t"
        "call _tes3x_bow_view_target@4\n\t"
        "testl %eax, %eax\n\t"
        "jz 1f\n\t"
        "fildl _offset_z\n\t"
        "fadds (%eax)\n\t"
        "fstps (%eax)\n\t"
        "1:\n\t"
        "popal\n\t"
        "popfl\n\t"
        "ret\n\t");
}
