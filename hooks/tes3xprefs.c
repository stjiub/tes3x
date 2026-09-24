/* Apply build-selected preferences after the retail signed controls file loads. */

#include "tes3xlog.h"

#ifndef TES3X_CONTROLS_LOAD
#error "define TES3X_CONTROLS_LOAD to the retail controls loader"
#endif
#ifndef TES3X_CONTROLS_TABLE
#error "define TES3X_CONTROLS_TABLE to the runtime action binding table"
#endif
#ifndef TES3X_INVERT_LOOK
#error "define TES3X_INVERT_LOOK from the build profile"
#endif

#define ACTION_LOOK_UP 31
#define ACTION_LOOK_DOWN 32
#define INPUT_RIGHT_Y_POSITIVE 25
#define INPUT_RIGHT_Y_NEGATIVE 26

typedef void(__attribute__((thiscall)) *fn_controls_load)(void *);

static int row_has(const unsigned char *row, unsigned char input)
{
    return row[0] == input || row[1] == input;
}

void __attribute__((thiscall)) tes3x_preferences_hook(void *controls)
{
    unsigned char *bindings = (unsigned char *)TES3X_CONTROLS_TABLE;
    unsigned char *up = bindings + ACTION_LOOK_UP * 2;
    unsigned char *down = bindings + ACTION_LOOK_DOWN * 2;
    unsigned char wanted = TES3X_INVERT_LOOK
        ? INPUT_RIGHT_Y_POSITIVE : INPUT_RIGHT_Y_NEGATIVE;
    unsigned char a;
    unsigned char b;

    ((fn_controls_load)TES3X_CONTROLS_LOAD)(controls);

    if (!row_has(up, wanted)
            && row_has(up, TES3X_INVERT_LOOK
                       ? INPUT_RIGHT_Y_NEGATIVE : INPUT_RIGHT_Y_POSITIVE)
            && row_has(down, wanted)) {
        a = up[0];
        b = up[1];
        up[0] = down[0];
        up[1] = down[1];
        down[0] = a;
        down[1] = b;
    }

    tes3x_log("prefs.invert_look", TES3X_INVERT_LOOK);
    tes3x_log("prefs.look_up", up[0]);
    tes3x_log("prefs.look_down", down[0]);
}
