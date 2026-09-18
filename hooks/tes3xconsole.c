/* Make the in-game console reachable on a retail pad.
 *
 * The console is not missing from this build - Console::Toggle, the menu builder, the print path
 * and its exemption from the bulk menu-close are all intact, and the toggle is already called
 * from the main update loop. It is only unreachable: the call is gated on input action 25, and
 * action 25 waits on a latch bit nothing in the image ever sets, while the keyboard path bounds
 * actions below 25. It was switched off in data, not removed from code.
 *
 * So this replaces the gate rather than the console. The engine's own check at that one call site
 * becomes this predicate, which reads two inputs straight out of the active port's block and
 * returns whether both are held.
 *
 * Nothing here touches the binding table at 0x003C69C8 or t:\controls.dat. That file is the
 * player's own control scheme - the engine writes it as well as reads it - so binding the console
 * through it would both spend a button that play needs and be overwritten by any in-game rebind.
 */

#include "tes3x_thunks.h"
#include "tes3xlog.h"

#ifndef TES3X_INI_GET_STRING
#error "define TES3X_INI_GET_STRING to the VA of the ini string reader"
#endif
#ifndef TES3X_INI_PATH
#error "define TES3X_INI_PATH to the VA of the engine's ini filename string"
#endif

/* Input controller layout, from the poll that expands XINPUT_STATE into the indexed array.
 * Each port block is 0x200 bytes: 22 bytes of XINPUT_STATE, then 30 derived words. A digital
 * button reads 0x7FFF while held and 0 otherwise. */
#define CTRL_PORT 0x804   /* the active port, 0..3 */
#define PORT_STRIDE 0x200 /* per-port block */
#define INPUT_BASE 0x16   /* the derived array, index i at INPUT_BASE + i*2 */
#define INPUT_COUNT 30

/* Index 7 is Back and index 9 is the right thumb click. Back is the secondary binding for the
 * menu-open action; index 9 appears in no binding at all, which makes this the quietest pair of
 * buttons available. [Xbox] ConsoleCombo overrides it without a rebuild. */
#define COMBO_DEFAULT_A 7
#define COMBO_DEFAULT_B 9

typedef int(__stdcall *fn_ini_get_string)(const char *section, const char *key, const char *dflt,
                                          char *buf, int size, const char *file);

static int combo_a = COMBO_DEFAULT_A;
static int combo_b = COMBO_DEFAULT_B;
static int combo_ready;

/* "7,9" - two small decimal indices. Anything unparseable leaves the defaults alone. */
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
    if (n != 2)
        return 0;
    if (v[0] < 0 || v[0] >= INPUT_COUNT || v[1] < 0 || v[1] >= INPUT_COUNT)
        return 0;
    if (v[0] == v[1])
        return 0;
    combo_a = v[0];
    combo_b = v[1];
    return 1;
}

/* Deferred to the first call: the ini lives on D:, which is not mounted at the XBE entry point. */
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

/* Stands in for the engine's action check at its console call site. __thiscall with two stack
 * arguments, so the callee clears 8 bytes, matching the function it replaces. The action and mode
 * are ignored: this call site only ever asks about the console. */
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

    /* Consume both, so the actions they are otherwise bound to do not also fire this frame. The
     * array is rebuilt from XINPUT_STATE on every poll, so clearing it here carries no state. */
    in[combo_a] = 0;
    in[combo_b] = 0;
    tes3x_log("console.toggle", (u32)port);
    return 1;
}
