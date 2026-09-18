/* Make the in-game console reachable.
 *
 * Console::Toggle is already called from the update loop, gated on an input action that waits on
 * a latch bit nothing sets. This replaces that gate with a two-button check. The binding table
 * and t:\controls.dat are deliberately untouched - that file is the player's own control scheme.
 */

#include "tes3x_thunks.h"
#include "tes3xlog.h"

#ifndef TES3X_INI_GET_STRING
#error "define TES3X_INI_GET_STRING to the VA of the ini string reader"
#endif
#ifndef TES3X_INI_PATH
#error "define TES3X_INI_PATH to the VA of the engine's ini filename string"
#endif

/* Per-port block: 22 bytes of XINPUT_STATE, then 30 derived words. A held button reads 0x7FFF. */
#define CTRL_PORT 0x804
#define PORT_STRIDE 0x200
#define INPUT_BASE 0x16
#define INPUT_COUNT 30

/* 7 is Back, 9 is the right thumb click - the only index bound to nothing. */
#define COMBO_DEFAULT_A 7
#define COMBO_DEFAULT_B 9

typedef int(__stdcall *fn_ini_get_string)(const char *section, const char *key, const char *dflt,
                                          char *buf, int size, const char *file);

static int combo_a = COMBO_DEFAULT_A;
static int combo_b = COMBO_DEFAULT_B;
static int combo_ready;

/* "7,9". Anything unparseable leaves the defaults. */
static int parse_combo(const char *s)
{
    int v[2] = {-1, -1};
    int n = 0, digits = 0, acc = 0;

    for (;; s++) {
        if (*s >= '0' && *s <= '9') {
            acc = acc * 10 + (*s - '0');
            digits = 1;
            continue;
        }
        if (digits) {
            if (n < 2)
                v[n] = acc;
            n++;
            acc = 0;
            digits = 0;
        }
        if (*s == 0)
            break;
    }
    if (n != 2 || v[0] == v[1])
        return 0;
    if (v[0] < 0 || v[0] >= INPUT_COUNT || v[1] < 0 || v[1] >= INPUT_COUNT)
        return 0;
    combo_a = v[0];
    combo_b = v[1];
    return 1;
}

/* Deferred: D: is not mounted at the XBE entry point. */
static void load_combo(void)
{
    fn_ini_get_string get = (fn_ini_get_string)TES3X_INI_GET_STRING;
    char buf[32];
    int i;

    combo_ready = 1;
    for (i = 0; i < (int)sizeof(buf); i++)
        buf[i] = 0;

    get("Xbox", "ConsoleCombo", "", buf, (int)sizeof(buf) - 1, (const char *)TES3X_INI_PATH);
    if (buf[0] && parse_combo(buf))
        tes3x_log("console.combo_ini", (u32)((combo_a << 8) | combo_b));
    else
        tes3x_log("console.combo_default", (u32)((combo_a << 8) | combo_b));
}

/* Replaces the engine's action check at its console call site; ret 8 matches the original. */
unsigned int __attribute__((thiscall)) tes3x_console_hook(void *ctrl, int action, int mode)
{
    unsigned char *base = (unsigned char *)ctrl;
    short *in;
    int port;

    (void)action;
    (void)mode;

    if (!combo_ready)
        load_combo();

    if (!base) {
        tes3x_log("console.no_ctrl", 0);
        return 0;
    }

    port = *(int *)(base + CTRL_PORT);
    if (port < 0 || port > 3)
        return 0;

    in = (short *)(base + port * PORT_STRIDE + INPUT_BASE);
    if (!in[combo_a] || !in[combo_b])
        return 0;

    /* Consume both, so their own actions do not also fire. The array is rebuilt each poll. */
    in[combo_a] = 0;
    in[combo_b] = 0;
    tes3x_log("console.toggle", (u32)port);
    return 1;
}
